from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
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
