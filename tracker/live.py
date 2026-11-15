"""Live aircraft data for the map.

How it stays fast and polite when many people use it at once
-------------------------------------------------------------
* The world is split into fixed 5-degree cells. Every visitor looking at the
  same area uses the same cells, so one request to the data provider serves
  everyone, no matter how many people have the map open.
* Each cell is cached for a few seconds. When the cache expires, only one
  request refreshes it while everyone else keeps getting the previous copy.
* Calls to the provider are spaced out to respect its rate limit, and one
  visitor can only trigger a few of them at a time. A large area simply fills
  in over the next few refreshes.
* If the provider is down, the last good copy is served and marked "stale".
* In "auto" mode the site falls back to clearly labelled simulated traffic and
  stops retrying for a minute, so a broken feed never slows the site down.
"""
import hashlib
import json
import logging
import math
import time
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

CELL_DEGREES = 5
CELL_RADIUS_NM = 215  # covers the corners of a 5-degree cell (provider limit is 250)
MAX_CELLS = 9  # larger views must zoom in
MAX_AIRCRAFT = 800
MAX_FETCHES_PER_REQUEST = 3  # one visitor can never trigger a flood of provider calls
LOCK_SECONDS = 15
STALE_SECONDS = 300
LIVE_DOWN_SECONDS = 60
MIN_SECONDS_BETWEEN_CALLS = 1.1
DOWN_KEY = "aircraft:live-down"
LAST_CALL_KEY = "aircraft:last-call"

PUBLIC_FIELDS = ("hex", "callsign", "reg", "type", "lat", "lon", "alt", "speed", "track", "vrate")


class FeedError(Exception):
    """The live data source could not be reached or sent something unusable."""


class TooManyCells(Exception):
    """The map view is too large to cover with live queries."""


def _setting(name, default):
    return getattr(settings, name, default)


# ---------------------------------------------------------------------------
# Map cells
# ---------------------------------------------------------------------------


def cells_for_view(south, west, north, east):
    """Return the fixed grid cells that cover a map view."""
    first_row = math.floor((south + 90) / CELL_DEGREES)
    last_row = math.floor((north + 90) / CELL_DEGREES)
    first_col = math.floor((west + 180) / CELL_DEGREES)
    last_col = math.floor((east + 180) / CELL_DEGREES)
    cells = [
        (row, col)
        for row in range(first_row, last_row + 1)
        for col in range(first_col, last_col + 1)
    ]
    if len(cells) > MAX_CELLS:
        raise TooManyCells(f"{len(cells)} cells needed, the limit is {MAX_CELLS}")
    return cells


def cell_centre(cell):
    row, col = cell
    return (
        -90 + (row + 0.5) * CELL_DEGREES,
        -180 + (col + 0.5) * CELL_DEGREES,
    )


def cell_key(cell):
    return f"aircraft:cell:{cell[0]}:{cell[1]}"


# ---------------------------------------------------------------------------
# Talking to the data provider (ADS-B Exchange "v2" format, used by
# airplanes.live, adsb.lol, adsb.fi and others)
# ---------------------------------------------------------------------------


def _http_get_json(url):
    request = Request(
        url,
        headers={
            "User-Agent": _setting("AIRCRAFT_USER_AGENT", "SkyLog/1.0"),
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=_setting("AIRCRAFT_TIMEOUT", 6)) as response:
            return json.loads(response.read().decode("utf-8"))
    except (URLError, OSError, ValueError) as error:
        raise FeedError(str(error)) from error


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _whole(value):
    value = _number(value)
    return None if value is None else int(round(value))


def normalise(raw):
    """Turn one provider record into SkyLog's own simple format."""
    if not isinstance(raw, dict):
        return None
    lat, lon = _number(raw.get("lat")), _number(raw.get("lon"))
    hex_code = str(raw.get("hex") or "").strip().lower()
    if lat is None or lon is None or not hex_code:
        return None
    altitude = raw.get("alt_baro")
    on_ground = altitude == "ground"
    return {
        "hex": hex_code,
        "callsign": str(raw.get("flight") or "").strip(),
        "reg": str(raw.get("r") or "").strip().upper(),
        "type": str(raw.get("t") or "").strip().upper(),
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "alt": None if on_ground else _whole(altitude),
        "speed": _whole(raw.get("gs")),
        "track": _whole(raw.get("track")),
        "vrate": _whole(raw.get("baro_rate")),
        "ground": on_ground,
    }


def _parse_feed(data):
    records = data.get("ac") if isinstance(data, dict) else None
    if not isinstance(records, list):
        raise FeedError("The data provider sent an unexpected response.")
    return [item for item in map(normalise, records) if item]


def fetch_cell(cell):
    lat, lon = cell_centre(cell)
    base = _setting("AIRCRAFT_API_BASE", "https://api.airplanes.live/v2").rstrip("/")
    return _parse_feed(_http_get_json(f"{base}/point/{lat:.3f}/{lon:.3f}/{CELL_RADIUS_NM}"))


def fetch_registrations(registrations):
    base = _setting("AIRCRAFT_API_BASE", "https://api.airplanes.live/v2").rstrip("/")
    found = []
    chunk, size = [], 0
    for reg in registrations + [None]:  # None flushes the last chunk
        if reg is not None and size + len(reg) + 1 < 800:
            chunk.append(reg)
            size += len(reg) + 1
            continue
        if chunk:
            url = f"{base}/reg/{quote(','.join(chunk), safe=',')}"
            found.extend(_parse_feed(_http_get_json(url)))
        chunk, size = ([reg], len(reg) + 1) if reg is not None else ([], 0)
    return found


# ---------------------------------------------------------------------------
# Caching, rate limiting and sharing
# ---------------------------------------------------------------------------


def _respect_rate_limit():
    last = cache.get(LAST_CALL_KEY)
    if last is not None:
        wait = MIN_SECONDS_BETWEEN_CALLS - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
    cache.set(LAST_CALL_KEY, time.time(), 30)


def _cached(key, fetcher, may_fetch=True):
    """Return (fetched_at, items, was_stale, did_fetch), or None when nothing
    is cached and we are not allowed to ask the provider.

    The provider is called at most once per cache period, no matter how many
    visitors ask.
    """
    fresh = cache.get(key)
    if fresh is not None:
        return fresh[0], fresh[1], False, False

    stale = cache.get(key + ":stale")
    if not may_fetch:
        return (stale[0], stale[1], True, False) if stale is not None else None

    lock = key + ":lock"
    if not cache.add(lock, 1, LOCK_SECONDS) and stale is not None:
        return stale[0], stale[1], True, False  # someone else is refreshing it

    try:
        _respect_rate_limit()
        items = fetcher()
    except FeedError:
        cache.delete(lock)
        if stale is not None:
            return stale[0], stale[1], True, False
        raise
    entry = (time.time(), items)
    cache.set(key, entry, _setting("AIRCRAFT_CACHE_SECONDS", 15))
    cache.set(key + ":stale", entry, STALE_SECONDS)
    cache.delete(lock)
    return entry[0], items, False, True


def live_traffic(cells):
    aircraft, any_stale, updated = {}, False, 0
    fetches_left = MAX_FETCHES_PER_REQUEST
    for cell in cells:
        result = _cached(cell_key(cell), lambda c=cell: fetch_cell(c), may_fetch=fetches_left > 0)
        if result is None:  # not loaded yet, it will fill in on a later refresh
            any_stale = True
            continue
        fetched_at, items, was_stale, did_fetch = result
        fetches_left -= 1 if did_fetch else 0
        any_stale = any_stale or was_stale
        updated = max(updated, fetched_at)
        for item in items:
            aircraft[item["hex"]] = item
    return list(aircraft.values()), any_stale, updated or time.time()


def live_fleet(registrations):
    if not registrations:
        return [], False, time.time()
    digest = hashlib.sha1(",".join(sorted(registrations)).encode()).hexdigest()[:16]
    fetched_at, items, was_stale, _ = _cached(
        f"aircraft:fleet:{digest}", lambda: fetch_registrations(sorted(registrations))
    )
    return items, was_stale, fetched_at


# ---------------------------------------------------------------------------
# Simulated traffic (used when the live feed is not reachable)
# ---------------------------------------------------------------------------

AIRPORTS = {
    "LOS": (6.5774, 3.3212), "ABV": (9.0068, 7.2632), "KAN": (12.0476, 8.5246),
    "PHC": (5.0155, 6.9496), "ENU": (6.4743, 7.5619), "KAD": (10.6960, 7.3201),
    "ACC": (5.6052, -0.1668), "NIM": (13.4815, 2.1836), "DLA": (4.0061, 9.7195),
    "COO": (6.3572, 2.3844), "JOS": (9.6398, 8.8690), "BNI": (6.3170, 5.5995),
}
ROUTES = [
    ("LOS", "ABV"), ("ABV", "KAN"), ("PHC", "LOS"), ("ENU", "ABV"),
    ("LOS", "ACC"), ("KAD", "LOS"), ("KAN", "NIM"), ("LOS", "DLA"),
    ("ABV", "PHC"), ("COO", "ABV"), ("JOS", "LOS"), ("BNI", "KAN"),
]
DEMO_TYPES = ["B738", "A320", "E190", "DH8D", "B77W", "A332", "AT72"]
DEMO_TIME_SCALE = 6  # simulated planes move faster so the demo feels alive

# (name keywords, cruise altitude in feet, cruise speed in knots)
PROFILES = [
    (("cessna 172", "cessna 182", "piper", "cherokee", "cirrus", "diamond", "tecnam", "c172"), 8500, 115),
    (("caravan", "208", "pc-12", "pilatus"), 12000, 170),
    (("king air", "beech", "atr", "dash", "dhc", "dh8", "q400", "at72"), 24000, 280),
]
DEFAULT_PROFILE = (35000, 450)


def _profile(type_name):
    name = (type_name or "").lower()
    for keywords, altitude, speed in PROFILES:
        if any(keyword in name for keyword in keywords):
            return altitude, speed
    return DEFAULT_PROFILE


def _bearing(start, end):
    lat1, lon1, lat2, lon2 = map(math.radians, (start[0], start[1], end[0], end[1]))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _demo_flight(hex_code, callsign, reg, aircraft_type, route, period, offset, now):
    origin, destination = AIRPORTS[route[0]], AIRPORTS[route[1]]
    progress = ((now * DEMO_TIME_SCALE + offset) % period) / period
    leg = 1 - abs(2 * progress - 1)  # out to the destination and back again
    outbound = progress < 0.5
    cruise_altitude, cruise_speed = _profile(aircraft_type)
    height = min(1.0, leg / 0.15, (1 - leg) / 0.15)
    height = max(height, 0.05)
    if outbound:
        track = _bearing(origin, destination)
    else:
        track = (_bearing(origin, destination) + 180) % 360
    climbing = (outbound and leg < 0.5) or (not outbound and leg > 0.5)
    return {
        "hex": hex_code,
        "callsign": callsign,
        "reg": reg,
        "type": aircraft_type,
        "lat": round(origin[0] + (destination[0] - origin[0]) * leg, 4),
        "lon": round(origin[1] + (destination[1] - origin[1]) * leg, 4),
        "alt": int(cruise_altitude * height),
        "speed": int(cruise_speed * (0.4 + 0.6 * height)),
        "track": int(round(track)),
        "vrate": 0 if height >= 1 else (1 if climbing else -1) * max(400, int(1800 * cruise_altitude / 35000)),
        "ground": False,
    }


def demo_traffic(now):
    flights = []
    for index in range(36):
        route = ROUTES[index % len(ROUTES)]
        flights.append(
            _demo_flight(
                f"d{index:05x}", f"SKY{100 + index}", "", DEMO_TYPES[index % len(DEMO_TYPES)],
                route, 1800 + 150 * index, index * 410, now,
            )
        )
    return flights


def demo_fleet(fleet, now):
    """Put serviceable aircraft from the database into the simulated sky."""
    flights = []
    for index, row in enumerate(fleet):
        if row["status"] != "serviceable":
            continue
        route = ROUTES[(index * 3 + 1) % len(ROUTES)]
        flights.append(
            _demo_flight(
                f"f{index:05x}", row["registration"].replace("-", ""), row["registration"].upper(),
                row["type"], route, 1500 + 200 * index, index * 500, now,
            )
        )
    return flights


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------


def _public(flight):
    return {field: flight.get(field) for field in PUBLIC_FIELDS}


def get_feed(view, fleet):
    """Build the JSON for the map.

    view  -- (south, west, north, east)
    fleet -- list of dicts describing SkyLog's own aircraft
    """
    south, west, north, east = view
    cells = cells_for_view(south, west, north, east)  # raises TooManyCells
    provider = _setting("AIRCRAFT_PROVIDER", "auto")
    registrations = [row["registration"].upper() for row in fleet]

    traffic = fleet_flights = None
    source, updated = "demo", time.time()

    live_allowed = provider == "adsb" or (provider == "auto" and not cache.get(DOWN_KEY))
    if live_allowed:
        try:
            traffic, traffic_stale, traffic_time = live_traffic(cells)
            fleet_flights, fleet_stale, fleet_time = live_fleet(registrations)
            source = "live-stale" if (traffic_stale or fleet_stale) else "live"
            updated = max(traffic_time, fleet_time)
        except FeedError as error:
            log.warning("Live aircraft feed unavailable: %s", error)
            if provider == "adsb":
                raise
            cache.set(DOWN_KEY, 1, LIVE_DOWN_SECONDS)
            traffic = None

    if traffic is None:
        now = time.time()
        traffic, fleet_flights, source, updated = (
            demo_traffic(now), demo_fleet(fleet, now), "demo", now,
        )

    fleet_regs = set(registrations)
    visible = [
        _public(flight)
        for flight in traffic
        if not flight["ground"]
        and flight["reg"] not in fleet_regs
        and south <= flight["lat"] <= north
        and west <= flight["lon"] <= east
    ]
    visible.sort(key=lambda flight: -(flight["alt"] or 0))
    visible = visible[:MAX_AIRCRAFT]

    by_registration = {
        flight["reg"]: flight for flight in fleet_flights if flight["reg"] and not flight["ground"]
    }
    fleet_out = []
    for row in fleet:
        flight = by_registration.get(row["registration"].upper())
        fleet_out.append({**row, "live": _public(flight) if flight else None})

    return {
        "source": source,
        "updated": int(updated),
        "count": len(visible) + sum(1 for row in fleet_out if row["live"]),
        "aircraft": visible,
        "fleet": fleet_out,
    }
