from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from unittest import mock
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Aircraft, MaintenanceTask


class ModelTests(TestCase):
    def setUp(self):
        self.aircraft = Aircraft.objects.create(
            registration="5N-ABC", aircraft_type="Cessna 172"
        )

    def make_task(self, days_from_today, status=MaintenanceTask.Status.OPEN):
        return MaintenanceTask.objects.create(
            aircraft=self.aircraft,
            title="Oil change",
            due_date=timezone.localdate() + timedelta(days=days_from_today),
            status=status,
        )

    def test_aircraft_string_shows_registration_and_type(self):
        self.assertEqual(str(self.aircraft), "5N-ABC (Cessna 172)")

    def test_new_aircraft_is_serviceable(self):
        self.assertEqual(self.aircraft.status, Aircraft.Status.SERVICEABLE)

    def test_past_due_open_task_is_overdue(self):
        self.assertTrue(self.make_task(-3).is_overdue)

    def test_future_task_is_not_overdue(self):
        self.assertFalse(self.make_task(5).is_overdue)

    def test_finished_task_is_never_overdue(self):
        self.assertFalse(self.make_task(-3, MaintenanceTask.Status.DONE).is_overdue)


class ViewTests(TestCase):
    def setUp(self):
        self.aircraft = Aircraft.objects.create(
            registration="5N-ABC", aircraft_type="Cessna 172"
        )
        today = timezone.localdate()
        MaintenanceTask.objects.create(
            aircraft=self.aircraft,
            title="Overdue inspection",
            due_date=today - timedelta(days=2),
        )
        MaintenanceTask.objects.create(
            aircraft=self.aircraft,
            title="Tyre check",
            due_date=today + timedelta(days=9),
            priority=MaintenanceTask.Priority.HIGH,
        )

    def test_dashboard_counts_open_and_overdue_tasks(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["open_count"], 2)
        self.assertEqual(response.context["overdue_count"], 1)

    def test_aircraft_list_shows_open_task_count(self):
        response = self.client.get(reverse("aircraft_list"))
        self.assertEqual(response.context["aircraft_list"][0].open_tasks, 2)

    def test_aircraft_detail_page_loads(self):
        response = self.client.get(reverse("aircraft_detail", args=[self.aircraft.pk]))
        self.assertContains(response, "5N-ABC")

    def test_unknown_aircraft_returns_404(self):
        response = self.client.get(reverse("aircraft_detail", args=[999]))
        self.assertEqual(response.status_code, 404)

    def test_task_search_filters_results(self):
        response = self.client.get(reverse("task_list"), {"q": "tyre"})
        self.assertEqual(len(response.context["tasks"]), 1)

    def test_task_priority_filter(self):
        response = self.client.get(reverse("task_list"), {"priority": "high"})
        self.assertEqual(len(response.context["tasks"]), 1)

    def test_home_page_loads_with_fleet_numbers(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "airworthy")
        self.assertEqual(response.context["total_aircraft"], 1)
        self.assertEqual(response.context["overdue_count"], 1)

    def test_dashboard_has_donut_chart_data(self):
        response = self.client.get(reverse("dashboard"))
        self.assertIn("conic-gradient", response.context["donut"])


class EditingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("umar", password="pass12345")
        self.aircraft = Aircraft.objects.create(
            registration="5N-ABC", aircraft_type="Cessna 172"
        )
        self.task = MaintenanceTask.objects.create(
            aircraft=self.aircraft, title="Oil change", due_date=timezone.localdate()
        )

    def task_data(self, **overrides):
        data = {
            "aircraft": self.aircraft.pk,
            "title": "Brake check",
            "description": "",
            "priority": "high",
            "status": "open",
            "due_date": "2026-12-01",
        }
        data.update(overrides)
        return data

    def test_visitors_are_sent_to_login(self):
        response = self.client.get(reverse("task_create"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_visitors_cannot_post_changes(self):
        self.client.post(reverse("task_create"), self.task_data())
        self.assertEqual(MaintenanceTask.objects.count(), 1)

    def test_logged_in_user_can_add_a_task(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("task_create"), self.task_data())
        self.assertRedirects(response, reverse("task_list"))
        self.assertEqual(MaintenanceTask.objects.count(), 2)

    def test_logged_in_user_can_edit_a_task(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse("task_edit", args=[self.task.pk]),
            self.task_data(title="Changed title", status="done"),
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "Changed title")
        self.assertEqual(self.task.status, "done")

    def test_logged_in_user_can_delete_a_task(self):
        self.client.force_login(self.user)
        self.client.post(reverse("task_delete", args=[self.task.pk]))
        self.assertEqual(MaintenanceTask.objects.count(), 0)

    def test_registration_is_saved_in_capitals(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse("aircraft_create"),
            {"registration": " 5n-new ", "aircraft_type": "Cessna 208",
             "status": "serviceable", "total_hours": 10},
        )
        self.assertTrue(Aircraft.objects.filter(registration="5N-NEW").exists())

    def test_duplicate_registration_shows_an_error(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("aircraft_create"),
            {"registration": "5n-abc", "aircraft_type": "Other",
             "status": "serviceable", "total_hours": 1},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("registration", response.context["form"].errors)

    def test_add_button_only_shows_for_logged_in_users(self):
        page = reverse("task_list")
        self.assertNotContains(self.client.get(page), "Add task")
        self.client.force_login(self.user)
        self.assertContains(self.client.get(page), "Add task")

    def test_login_page_loads_and_logout_redirects_home(self):
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        self.client.force_login(self.user)
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("home"))


class SeedDemoTests(TestCase):
    def test_seed_demo_adds_sample_data_once(self):
        call_command("seed_demo", verbosity=0)
        self.assertEqual(Aircraft.objects.count(), 4)
        self.assertEqual(MaintenanceTask.objects.count(), 8)
        call_command("seed_demo", verbosity=0)
        self.assertEqual(Aircraft.objects.count(), 4)


LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# One record in the shape the data provider sends
SAMPLE_FEED = {
    "ac": [
        {"hex": "ABC123", "flight": "ARK501 ", "r": "5n-abc", "t": "c172", "lat": 9.5,
         "lon": 7.5, "alt_baro": 4500, "gs": 110.4, "track": 270.2, "baro_rate": 0},
        {"hex": "def456", "flight": "AFR22", "r": "F-ABCD", "t": "A332", "lat": 8.0,
         "lon": 6.0, "alt_baro": 36000, "gs": 480, "track": 10, "baro_rate": -64},
        {"hex": "aaa111", "flight": "TAXI1", "lat": 9.1, "lon": 7.0, "alt_baro": "ground"},
        {"hex": "bbb222", "flight": "NOPOS"},
    ]
}
# A view of Nigeria that needs only a few grid cells
NIGERIA = (5.0, 3.0, 12.0, 11.0)
# A small view that needs just one grid cell
SMALL = (5.0, 5.0, 9.9, 9.0)


@override_settings(CACHES=LOCMEM, AIRCRAFT_PROVIDER="adsb")
class LiveFeedTests(TestCase):
    def setUp(self):
        cache.clear()
        self.fleet = [
            {"id": 1, "registration": "5N-ABC", "type": "Cessna 172", "status": "serviceable",
             "status_label": "Serviceable", "open_tasks": 2},
            {"id": 2, "registration": "5N-QRS", "type": "Piper", "status": "grounded",
             "status_label": "Grounded", "open_tasks": 0},
        ]
        sleeper = mock.patch("tracker.live.time.sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_nearby_views_share_the_same_cells(self):
        from tracker import live

        self.assertEqual(
            live.cells_for_view(5.1, 3.1, 9.8, 7.9), live.cells_for_view(5.3, 3.3, 9.6, 7.7)
        )

    def test_a_view_that_is_too_large_is_rejected(self):
        from tracker import live

        with self.assertRaises(live.TooManyCells):
            live.cells_for_view(-30, -30, 60, 60)

    def test_provider_records_are_cleaned_up(self):
        from tracker import live

        plane = live.normalise(SAMPLE_FEED["ac"][0])
        self.assertEqual(plane["hex"], "abc123")
        self.assertEqual(plane["callsign"], "ARK501")
        self.assertEqual(plane["reg"], "5N-ABC")
        self.assertEqual(plane["type"], "C172")
        self.assertEqual((plane["alt"], plane["speed"], plane["track"]), (4500, 110, 270))
        self.assertTrue(live.normalise(SAMPLE_FEED["ac"][2])["ground"])
        self.assertIsNone(live.normalise(SAMPLE_FEED["ac"][3]))
        self.assertIsNone(live.normalise("not a record"))

    def test_fleet_aircraft_are_matched_and_removed_from_general_traffic(self):
        from tracker import live

        with mock.patch("tracker.live._http_get_json", return_value=SAMPLE_FEED):
            feed = live.get_feed(SMALL, self.fleet)
        self.assertEqual(feed["source"], "live")
        self.assertEqual([plane["hex"] for plane in feed["aircraft"]], ["def456"])
        in_air = {row["registration"]: row["live"] for row in feed["fleet"]}
        self.assertEqual(in_air["5N-ABC"]["callsign"], "ARK501")
        self.assertIsNone(in_air["5N-QRS"])

    def test_ground_traffic_is_not_shown(self):
        from tracker import live

        with mock.patch("tracker.live._http_get_json", return_value=SAMPLE_FEED):
            feed = live.get_feed(SMALL, [])
        self.assertNotIn("aaa111", [plane["hex"] for plane in feed["aircraft"]])

    def test_many_visitors_cause_one_request_to_the_provider(self):
        from tracker import live

        with mock.patch("tracker.live._http_get_json", return_value=SAMPLE_FEED) as http:
            live.get_feed(SMALL, [])
            calls_after_first_visitor = http.call_count
            for _ in range(50):
                live.get_feed(SMALL, [])
        self.assertEqual(http.call_count, calls_after_first_visitor)

    def test_old_data_is_served_when_the_provider_fails(self):
        from tracker import live

        with mock.patch("tracker.live._http_get_json", return_value=SAMPLE_FEED):
            live.get_feed(SMALL, [])
        for cell in live.cells_for_view(*SMALL):
            cache.delete(live.cell_key(cell))  # the fresh copy expires
        with mock.patch("tracker.live._http_get_json", side_effect=live.FeedError("down")):
            feed = live.get_feed(SMALL, [])
        self.assertEqual(feed["source"], "live-stale")
        self.assertTrue(feed["aircraft"])

    def test_live_only_mode_reports_an_error_when_the_provider_is_down(self):
        from tracker import live

        with mock.patch("tracker.live._http_get_json", side_effect=live.FeedError("down")):
            with self.assertRaises(live.FeedError):
                live.get_feed(NIGERIA, [])
            response = self.client.get(
                reverse("aircraft_feed"),
                {"south": 5, "west": 3, "north": 12, "east": 11},
            )
        self.assertEqual(response.status_code, 502)

    @override_settings(AIRCRAFT_PROVIDER="auto")
    def test_auto_mode_falls_back_to_simulated_traffic_and_stops_retrying(self):
        from tracker import live

        with mock.patch("tracker.live._http_get_json", side_effect=live.FeedError("down")) as http:
            first = live.get_feed(NIGERIA, self.fleet)
            attempts = http.call_count
            second = live.get_feed(NIGERIA, self.fleet)
        self.assertEqual((first["source"], second["source"]), ("demo", "demo"))
        self.assertEqual(http.call_count, attempts)  # no new attempts for a minute

    @override_settings(AIRCRAFT_PROVIDER="demo")
    def test_simulated_sky_has_serviceable_fleet_aircraft_only(self):
        from tracker import live

        feed = live.get_feed(NIGERIA, self.fleet)
        self.assertEqual(feed["source"], "demo")
        in_air = {row["registration"]: row["live"] for row in feed["fleet"]}
        self.assertIsNotNone(in_air["5N-ABC"])
        self.assertIsNone(in_air["5N-QRS"])  # grounded aircraft stay on the ground

    def test_one_request_never_triggers_a_flood_of_provider_calls(self):
        from tracker import live

        with mock.patch("tracker.live._http_get_json", return_value=SAMPLE_FEED) as http:
            live.get_feed(NIGERIA, [])
            self.assertEqual(http.call_count, live.MAX_FETCHES_PER_REQUEST)
            live.get_feed(NIGERIA, [])  # the next refresh fills in the rest
            self.assertEqual(http.call_count, len(live.cells_for_view(*NIGERIA)))
            live.get_feed(NIGERIA, [])
            self.assertEqual(http.call_count, len(live.cells_for_view(*NIGERIA)))

    @override_settings(AIRCRAFT_PROVIDER="demo")
    def test_simulated_small_aircraft_fly_like_small_aircraft(self):
        from tracker import live

        fleet = [{"id": 1, "registration": "5N-ABC", "type": "Cessna 172", "status": "serviceable",
                  "status_label": "Serviceable", "open_tasks": 0}]
        flight = live.get_feed(NIGERIA, fleet)["fleet"][0]["live"]
        self.assertLessEqual(flight["alt"], 9000)
        self.assertLessEqual(flight["speed"], 130)

    @override_settings(AIRCRAFT_PROVIDER="demo")
    def test_simulated_traffic_stays_inside_the_requested_area(self):
        from tracker import live

        feed = live.get_feed((6.0, 3.0, 10.0, 9.0), [])
        self.assertTrue(feed["aircraft"])
        for plane in feed["aircraft"]:
            self.assertTrue(6.0 <= plane["lat"] <= 10.0 and 3.0 <= plane["lon"] <= 9.0)


@override_settings(CACHES=LOCMEM, AIRCRAFT_PROVIDER="demo")
class FeedEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        Aircraft.objects.create(registration="5N-ABC", aircraft_type="Cessna 172")

    def get(self, **params):
        return self.client.get(reverse("aircraft_feed"), params)

    def test_valid_request_returns_traffic_and_the_fleet(self):
        response = self.get(south=5, west=3, north=12, east=11)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["source"], "demo")
        self.assertEqual(data["fleet"][0]["registration"], "5N-ABC")
        self.assertIn("Cache-Control", response)

    def test_missing_or_invalid_numbers_are_rejected(self):
        self.assertEqual(self.get().status_code, 400)
        self.assertEqual(self.get(south="x", west=1, north=2, east=3).status_code, 400)
        self.assertEqual(self.get(south="nan", west=1, north=2, east=3).status_code, 400)
        self.assertEqual(self.get(south=10, west=1, north=5, east=3).status_code, 400)

    def test_a_huge_area_asks_the_visitor_to_zoom_in(self):
        response = self.get(south=-60, west=-120, north=70, east=120)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "zoom_in")

    def test_map_page_loads(self):
        response = self.client.get(reverse("live_map"))
        self.assertContains(response, 'id="map"')
