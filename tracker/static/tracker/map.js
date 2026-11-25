(function () {
  var mapEl = document.getElementById("map");
  if (!mapEl || typeof L === "undefined") { return; }

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var feedUrl = mapEl.getAttribute("data-feed");
  var aircraftUrl = mapEl.getAttribute("data-aircraft-url");
  var centre = mapEl.getAttribute("data-center").split(",").map(Number);
  var startZoom = parseInt(mapEl.getAttribute("data-zoom"), 10);
  var POLL_MS = 10000;

  var statusEl = document.getElementById("feed-status");
  var noteEl = document.getElementById("feed-note");
  var fleetEl = document.getElementById("fleet-list");
  var detailEl = document.getElementById("detail");
  var findForm = document.getElementById("find-form");
  var findInput = document.getElementById("find-input");
  var findMsg = document.getElementById("find-msg");

  var PLANE_SVG =
    '<svg viewBox="0 0 64 64" aria-hidden="true"><path fill="currentColor" ' +
    'd="M32 2c2 0 3 4 3 8v14l27 14v6L35 36v14l7 6v4l-10-3-10 3v-4l7-6V36L2 44v-6l27-14V10c0-4 1-8 3-8z"/></svg>';

  var map = L.map(mapEl, { worldCopyJump: true, minZoom: 4, maxZoom: 12 }).setView(centre, startZoom);
  L.tileLayer(mapEl.getAttribute("data-tiles"), {
    maxZoom: 12,
    attribution: mapEl.getAttribute("data-attribution")
  }).addTo(map);

  window.skylogMap = map;

  /* Keep the map correctly sized when the sidebar or full screen changes the layout */
  if ("ResizeObserver" in window) {
    new ResizeObserver(function () { map.invalidateSize(); }).observe(mapEl);
  }

  /* Full screen: use the browser's full screen when available, otherwise fill the window */
  var layoutEl = document.querySelector(".map-layout");
  var fsBtn = document.getElementById("fullscreen-btn");
  function isFull() {
    return !!(document.fullscreenElement || document.webkitFullscreenElement) || layoutEl.classList.contains("is-expanded");
  }
  function syncFsLabel() { fsBtn.textContent = isFull() ? "Exit full screen" : "Full screen"; }
  function setExpanded(on) {
    layoutEl.classList.toggle("is-expanded", on);
    document.body.classList.toggle("no-scroll", on);
    syncFsLabel();
    setTimeout(function () { map.invalidateSize(); }, 60);
  }
  fsBtn.addEventListener("click", function () {
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      return;
    }
    if (layoutEl.classList.contains("is-expanded")) { setExpanded(false); return; }
    var request = layoutEl.requestFullscreen || layoutEl.webkitRequestFullscreen;
    if (request) {
      var result = request.call(layoutEl);
      if (result && result.catch) { result.catch(function () { setExpanded(true); }); }
    } else {
      setExpanded(true);
    }
  });
  document.addEventListener("fullscreenchange", syncFsLabel);
  document.addEventListener("webkitfullscreenchange", syncFsLabel);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && layoutEl.classList.contains("is-expanded")) { setExpanded(false); }
  });

  var markers = {};   // key -> Leaflet marker
  var planes = {};    // key -> { plane, fleet }
  var selectedKey = null;
  var lastFleet = [];
  var status = { source: "", updated: 0, count: 0 };
  var inFlight = null;
  var moveTimer = null;

  /* ---------- small helpers (text is always added as text, never as HTML) ---------- */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
  function num(value) { var n = Number(value); return isFinite(n) ? n : 0; }

  function fmtAlt(alt) {
    if (alt === null || alt === undefined) { return "n/a"; }
    return alt >= 18000 ? "FL" + Math.round(alt / 100) : alt.toLocaleString() + " ft";
  }
  function fmtSpeed(speed) { return speed === null || speed === undefined ? "n/a" : speed + " kt"; }
  function fmtTrack(track) { return track === null || track === undefined ? "n/a" : track + "\u00b0"; }
  function fmtVrate(rate) {
    if (rate === null || rate === undefined || Math.abs(rate) < 100) { return "level"; }
    return (rate > 0 ? "+" : "") + rate.toLocaleString() + " ft/min";
  }
  function title(plane, fleet) {
    if (fleet) { return fleet.registration; }
    return plane.callsign || plane.reg || plane.hex.toUpperCase();
  }

  /* ---------- markers ---------- */

  function makeIcon(plane, isFleet) {
    var size = isFleet ? 36 : 28;
    return L.divIcon({
      className: "plane-marker" + (isFleet ? " plane-fleet" : ""),
      html: '<span class="plane-rot" style="transform: rotate(' + num(plane.track) + 'deg)">' + PLANE_SVG + "</span>",
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2]
    });
  }

  function upsert(key, plane, fleet) {
    var isFleet = !!fleet;
    planes[key] = { plane: plane, fleet: fleet || null };
    var marker = markers[key];
    if (marker) {
      marker.setLatLng([plane.lat, plane.lon]);
      var node = marker.getElement();
      var rot = node && node.querySelector(".plane-rot");
      if (rot) { rot.style.transform = "rotate(" + num(plane.track) + "deg)"; }
      marker.setTooltipContent(title(plane, fleet));
      return;
    }
    marker = L.marker([plane.lat, plane.lon], {
      icon: makeIcon(plane, isFleet),
      keyboard: false,
      zIndexOffset: isFleet ? 1000 : 0
    }).addTo(map);
    marker.bindTooltip(title(plane, fleet), { direction: "top", offset: [0, -10], permanent: isFleet, className: isFleet ? "fleet-label" : "" });
    marker.on("click", function () { select(key, false); });
    markers[key] = marker;
    // Start gliding between updates only after the marker has been placed
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        var node = marker.getElement();
        if (node) { node.classList.add("ready"); }
      });
    });
  }

  function sync(data) {
    var seen = {};
    data.aircraft.forEach(function (plane) {
      var key = "hex:" + plane.hex;
      upsert(key, plane, null);
      seen[key] = true;
    });
    data.fleet.forEach(function (row) {
      if (!row.live) { return; }
      var key = "fleet:" + row.registration;
      upsert(key, row.live, row);
      seen[key] = true;
    });
    Object.keys(markers).forEach(function (key) {
      if (!seen[key]) {
        map.removeLayer(markers[key]);
        delete markers[key];
        delete planes[key];
        if (selectedKey === key) { selectedKey = null; showDetail(null); }
      }
    });
    if (selectedKey) { showDetail(selectedKey); highlight(); }
  }

  /* ---------- side panels ---------- */

  function highlight() {
    Object.keys(markers).forEach(function (key) {
      var node = markers[key].getElement();
      if (node) { node.classList.toggle("is-selected", key === selectedKey); }
    });
  }

  function fact(list, label, value) {
    var wrap = el("div");
    wrap.appendChild(el("dt", null, label));
    wrap.appendChild(el("dd", null, value));
    list.appendChild(wrap);
  }

  function showDetail(key) {
    clear(detailEl);
    var entry = key ? planes[key] : null;
    if (!entry) {
      detailEl.appendChild(el("p", "muted", "Click an aircraft on the map to see its details."));
      return;
    }
    var plane = entry.plane, fleet = entry.fleet;
    detailEl.appendChild(el("p", "detail-title", title(plane, fleet)));
    if (fleet) {
      detailEl.appendChild(el("p", "detail-sub", fleet.type + " \u00b7 " + fleet.status_label + " \u00b7 " + fleet.open_tasks + " open task" + (fleet.open_tasks === 1 ? "" : "s")));
    } else if (plane.type || plane.reg) {
      detailEl.appendChild(el("p", "detail-sub", [plane.type, plane.reg].filter(Boolean).join(" \u00b7 ")));
    }
    var facts = el("dl", "facts");
    if (plane.callsign) { fact(facts, "Callsign", plane.callsign); }
    fact(facts, "Altitude", fmtAlt(plane.alt));
    fact(facts, "Speed", fmtSpeed(plane.speed));
    fact(facts, "Heading", fmtTrack(plane.track));
    fact(facts, "Climb", fmtVrate(plane.vrate));
    fact(facts, "Transponder", plane.hex.toUpperCase());
    detailEl.appendChild(facts);
    if (fleet) {
      var link = el("a", "btn-app btn-quiet", "Open in SkyLog");
      link.href = aircraftUrl + num(fleet.id) + "/";
      detailEl.appendChild(link);
    }
  }

  function select(key, fly) {
    selectedKey = key;
    showDetail(key);
    highlight();
    var entry = planes[key];
    if (fly && entry) {
      var zoom = Math.max(map.getZoom(), 8);
      if (reduce) { map.setView([entry.plane.lat, entry.plane.lon], zoom); }
      else { map.flyTo([entry.plane.lat, entry.plane.lon], zoom, { duration: 1 }); }
    }
  }

  function renderFleet(fleet) {
    lastFleet = fleet;
    clear(fleetEl);
    if (!fleet.length) {
      fleetEl.appendChild(el("li", "muted", "No aircraft in your fleet yet."));
      return;
    }
    fleet.forEach(function (row) {
      var li = el("li");
      var button = el("button", "fleet-item" + (row.live ? " is-airborne" : ""));
      button.type = "button";
      var left = el("span", "fleet-left");
      left.appendChild(el("strong", null, row.registration));
      left.appendChild(el("span", "muted", row.type));
      var right = el("span", "fleet-right");
      if (row.live) {
        right.appendChild(el("span", "badge badge-serviceable", "In the air"));
        right.appendChild(el("span", "muted", fmtAlt(row.live.alt) + " \u00b7 " + fmtSpeed(row.live.speed)));
      } else {
        right.appendChild(el("span", "badge badge-" + row.status, row.status_label));
        right.appendChild(el("span", "muted", "Not in the air"));
      }
      button.appendChild(left);
      button.appendChild(right);
      button.addEventListener("click", function () {
        if (row.live) { select("fleet:" + row.registration, true); }
        else { window.location.href = aircraftUrl + num(row.id) + "/"; }
      });
      li.appendChild(button);
      fleetEl.appendChild(li);
    });
  }

  /* ---------- status line ---------- */

  function setChip(kind, text) {
    statusEl.className = "feed-chip is-" + kind;
    statusEl.textContent = text;
  }

  var diagnosticsUrl = mapEl.getAttribute("data-diagnostics");

  function showNote(text, withDiagnostics) {
    if (!text) { noteEl.hidden = true; return; }
    noteEl.textContent = text;
    if (withDiagnostics && diagnosticsUrl) {
      var link = el("a", null, "Staff: see why");
      link.href = diagnosticsUrl;
      link.target = "_blank";
      link.rel = "noopener";
      noteEl.appendChild(document.createTextNode(" "));
      noteEl.appendChild(link);
    }
    noteEl.hidden = false;
  }

  function renderStatus() {
    if (!status.source) { return; }
    var planesText = status.count + " aircraft";
    if (status.source === "demo") {
      setChip("demo", "Simulated traffic \u00b7 " + planesText);
      showNote("Live data is not connected on this server, so you are seeing simulated traffic. Your fleet details are real.", true);
    } else {
      var age = Math.max(0, Math.round(Date.now() / 1000 - status.updated));
      var label = status.source === "live-stale" ? "Delayed" : "Live";
      setChip(status.source === "live-stale" ? "stale" : "live", label + " \u00b7 " + planesText + " \u00b7 updated " + age + "s ago");
      showNote(status.source === "live-stale" ? "The live feed is slow right now, so these positions may be a little old." : "");
    }
  }

  /* ---------- fetching ---------- */

  function fetchFeed() {
    if (document.hidden) { return; }
    if (inFlight) { inFlight.abort(); }
    var bounds = map.getBounds();
    var url = feedUrl +
      "?south=" + bounds.getSouth().toFixed(3) +
      "&west=" + bounds.getWest().toFixed(3) +
      "&north=" + bounds.getNorth().toFixed(3) +
      "&east=" + bounds.getEast().toFixed(3);
    var controller = new AbortController();
    inFlight = controller;
    fetch(url, { signal: controller.signal, headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json().then(function (body) { return { ok: response.ok, body: body }; });
      })
      .then(function (result) {
        inFlight = null;
        if (!result.ok) {
          status.source = "";
          if (result.body.error === "zoom_in") {
            setChip("stale", "Zoom in to see aircraft");
            showNote("This view is too large. Zoom in a little to load live traffic.");
          } else {
            setChip("error", "Live feed unavailable");
            showNote(result.body.message || "Could not load aircraft. Retrying\u2026");
          }
          return;
        }
        status = { source: result.body.source, updated: result.body.updated, count: result.body.count };
        sync(result.body);
        renderFleet(result.body.fleet);
        renderStatus();
      })
      .catch(function (error) {
        if (error.name === "AbortError") { return; }
        inFlight = null;
        status.source = "";
        setChip("error", "Connection problem. Retrying\u2026");
      });
  }

  /* ---------- search ---------- */

  findForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var query = findInput.value.trim().toLowerCase();
    findMsg.textContent = "";
    if (!query) { return; }
    var found = null;
    Object.keys(planes).forEach(function (key) {
      if (found) { return; }
      var p = planes[key].plane, f = planes[key].fleet;
      var haystack = [p.callsign, p.reg, p.hex, f ? f.registration : ""].join(" ").toLowerCase();
      if (haystack.indexOf(query) !== -1) { found = key; }
    });
    if (found) { select(found, true); }
    else { findMsg.textContent = "No aircraft matching \u201c" + findInput.value.trim() + "\u201d in this area. Try zooming out or moving the map."; }
  });

  /* ---------- events ---------- */

  map.on("zoomstart", function () { mapEl.classList.add("is-zooming"); });
  map.on("zoomend", function () { mapEl.classList.remove("is-zooming"); });
  map.on("moveend", function () {
    clearTimeout(moveTimer);
    moveTimer = setTimeout(fetchFeed, 500);
  });
  map.on("click", function () { selectedKey = null; showDetail(null); highlight(); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) { fetchFeed(); } });

  fetchFeed();
  setInterval(fetchFeed, POLL_MS);
  setInterval(renderStatus, 1000);
})();
