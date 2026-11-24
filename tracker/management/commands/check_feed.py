from django.core.management.base import BaseCommand
from django.test.utils import override_settings

from tracker import live


class Command(BaseCommand):
    help = "Test the live aircraft feed and show exactly what it sends back."

    def add_arguments(self, parser):
        parser.add_argument("--base", help="Provider base address, e.g. https://opendata.adsb.fi/api")
        parser.add_argument("--point-path", help="Path pattern, e.g. v3/lat/{lat}/lon/{lon}/dist/{radius}")
        parser.add_argument("--reg-path", help="Registration path pattern")

    def handle(self, *args, **options):
        overrides = {}
        if options["base"]:
            overrides["AIRCRAFT_API_BASE"] = options["base"]
        if options["point_path"]:
            overrides["AIRCRAFT_POINT_PATH"] = options["point_path"]
        if options["reg_path"]:
            overrides["AIRCRAFT_REG_PATH"] = options["reg_path"]

        with override_settings(**overrides):
            cell = live.cells_for_view(5, 3, 12, 11)[0]  # an area over Nigeria
            url = live.cell_url(cell)
            self.stdout.write(f"Asking: {url}")
            try:
                raw = live._http_get_json(url)
            except live.FeedError as error:
                self.stdout.write(self.style.ERROR(f"FAILED: {error}"))
                return
            fields = ", ".join(raw) if isinstance(raw, dict) else type(raw).__name__
            self.stdout.write(f"Answer received. Top-level fields: {fields}")
            try:
                aircraft = live._parse_feed(raw)
            except live.FeedError as error:
                self.stdout.write(self.style.ERROR(f"FAILED: {error}"))
                return
            self.stdout.write(
                self.style.SUCCESS(f"OK: {len(aircraft)} aircraft understood in this area.")
            )
            for plane in aircraft[:3]:
                self.stdout.write(
                    f"  {plane['hex']} {plane['callsign'] or '-'} {plane['reg'] or '-'} "
                    f"{plane['type'] or '-'} alt={plane['alt']}"
                )
            self.stdout.write("Fleet lookup address would be: " + live.registrations_url(["5N-ABC"]))
