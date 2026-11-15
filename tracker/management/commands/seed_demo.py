from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from tracker.models import Aircraft, MaintenanceTask

AIRCRAFT = [
    ("5N-ABC", "Cessna 172", "serviceable", 3420),
    ("5N-XYZ", "Beechcraft King Air 350", "maintenance", 8120),
    ("5N-QRS", "Piper PA-28 Cherokee", "grounded", 5210),
    ("5N-DEF", "Cessna 208 Caravan", "serviceable", 1980),
]

# (registration, title, days from today, priority, status)
TASKS = [
    ("5N-ABC", "100-hour inspection", -4, "high", "open"),
    ("5N-ABC", "Replace nose tyre", 6, "medium", "open"),
    ("5N-XYZ", "Propeller overhaul", -1, "high", "in_progress"),
    ("5N-XYZ", "Avionics software update", 12, "low", "open"),
    ("5N-QRS", "Engine compression test", -9, "high", "open"),
    ("5N-QRS", "Brake pad replacement", 3, "medium", "in_progress"),
    ("5N-DEF", "Oil and filter change", 8, "low", "open"),
    ("5N-DEF", "Annual inspection", -20, "medium", "done"),
]


class Command(BaseCommand):
    help = "Add sample aircraft and tasks so a new site has something to show."

    def handle(self, *args, **options):
        if Aircraft.objects.exists():
            self.stdout.write("Aircraft already exist, so nothing was added.")
            return

        today = timezone.localdate()
        fleet = {}
        for registration, aircraft_type, status, hours in AIRCRAFT:
            fleet[registration] = Aircraft.objects.create(
                registration=registration,
                aircraft_type=aircraft_type,
                status=status,
                total_hours=hours,
            )
        for registration, title, days, priority, status in TASKS:
            MaintenanceTask.objects.create(
                aircraft=fleet[registration],
                title=title,
                due_date=today + timedelta(days=days),
                priority=priority,
                status=status,
            )
        self.stdout.write(
            f"Added {len(AIRCRAFT)} aircraft and {len(TASKS)} tasks."
        )
