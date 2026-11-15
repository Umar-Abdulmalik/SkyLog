import math

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, UpdateView

from . import live
from .forms import AircraftForm, TaskForm
from .models import Aircraft, MaintenanceTask


def _breakdown(counts, choices):
    """Turn {value: count} into rows for the CSS bar charts."""
    total = sum(counts.values()) or 1
    return [
        {
            "value": value,
            "label": label,
            "count": counts.get(value, 0),
            "percent": round(counts.get(value, 0) * 100 / total),
        }
        for value, label in choices
    ]


STATUS_COLOURS = {
    "serviceable": "var(--good)",
    "maintenance": "var(--runway)",
    "grounded": "var(--alert)",
}


def _donut_gradient(rows):
    """CSS conic-gradient used to draw the fleet status donut chart."""
    total = sum(row["count"] for row in rows)
    if total == 0:
        return "conic-gradient(var(--track) 0% 100%)"
    stops, start = [], 0.0
    for row in rows:
        if row["count"] == 0:
            continue
        end = start + row["count"] * 100 / total
        stops.append(f"{STATUS_COLOURS[row['value']]} {start:.1f}% {end:.1f}%")
        start = end
    return "conic-gradient(" + ", ".join(stops) + ")"


def home(request):
    open_tasks = MaintenanceTask.objects.exclude(status=MaintenanceTask.Status.DONE)
    context = {
        "total_aircraft": Aircraft.objects.count(),
        "open_count": open_tasks.count(),
        "overdue_count": open_tasks.filter(due_date__lt=timezone.localdate()).count(),
        "radar_aircraft": Aircraft.objects.all()[:4],
    }
    return render(request, "tracker/home.html", context)


def dashboard(request):
    today = timezone.localdate()
    open_tasks = MaintenanceTask.objects.exclude(status=MaintenanceTask.Status.DONE)

    fleet_counts = dict(
        Aircraft.objects.values_list("status").annotate(n=Count("id")).order_by()
    )
    priority_counts = dict(
        open_tasks.values_list("priority").annotate(n=Count("id")).order_by()
    )

    fleet_rows = _breakdown(fleet_counts, Aircraft.Status.choices)

    context = {
        "total_aircraft": Aircraft.objects.count(),
        "serviceable": fleet_counts.get(Aircraft.Status.SERVICEABLE, 0),
        "open_count": open_tasks.count(),
        "overdue_count": open_tasks.filter(due_date__lt=today).count(),
        "fleet_rows": fleet_rows,
        "donut": _donut_gradient(fleet_rows),
        "priority_rows": _breakdown(priority_counts, MaintenanceTask.Priority.choices),
        "upcoming": open_tasks.select_related("aircraft")[:6],
    }
    return render(request, "tracker/dashboard.html", context)


def aircraft_list(request):
    aircraft = Aircraft.objects.annotate(
        open_tasks=Count(
            "tasks", filter=~Q(tasks__status=MaintenanceTask.Status.DONE)
        )
    )
    return render(request, "tracker/aircraft_list.html", {"aircraft_list": aircraft})


def aircraft_detail(request, pk):
    aircraft = get_object_or_404(Aircraft, pk=pk)
    context = {"aircraft": aircraft, "tasks": aircraft.tasks.all()}
    return render(request, "tracker/aircraft_detail.html", context)


def task_list(request):
    tasks = MaintenanceTask.objects.select_related("aircraft")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    priority = request.GET.get("priority", "")

    if query:
        tasks = tasks.filter(
            Q(title__icontains=query) | Q(aircraft__registration__icontains=query)
        )
    if status:
        tasks = tasks.filter(status=status)
    if priority:
        tasks = tasks.filter(priority=priority)

    context = {
        "tasks": tasks,
        "query": query,
        "status": status,
        "priority": priority,
        "status_choices": MaintenanceTask.Status.choices,
        "priority_choices": MaintenanceTask.Priority.choices,
    }
    return render(request, "tracker/task_list.html", context)


# ---------------------------------------------------------------------------
# Login and editing (only logged-in users can change data)
# ---------------------------------------------------------------------------


class SkyLoginView(LoginView):
    template_name = "tracker/login.html"
    next_page = "dashboard"
    redirect_authenticated_user = True


class EditorMixin(LoginRequiredMixin, SuccessMessageMixin):
    """Shared settings for every page that changes data."""

    login_url = "login"
    template_name = "tracker/form.html"
    heading = ""
    submit_label = "Save"

    def get_cancel_url(self):
        return reverse("dashboard")

    def get_delete_url(self):
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["heading"] = self.heading
        context["submit_label"] = self.submit_label
        context["cancel_url"] = self.get_cancel_url()
        context["delete_url"] = self.get_delete_url()
        return context


class AircraftCreateView(EditorMixin, CreateView):
    model = Aircraft
    form_class = AircraftForm
    heading = "Add aircraft"
    submit_label = "Add aircraft"
    success_message = "Aircraft added."

    def get_cancel_url(self):
        return reverse("aircraft_list")

    def get_success_url(self):
        return reverse("aircraft_detail", args=[self.object.pk])


class AircraftUpdateView(EditorMixin, UpdateView):
    model = Aircraft
    form_class = AircraftForm
    heading = "Edit aircraft"
    submit_label = "Save changes"
    success_message = "Aircraft updated."

    def get_cancel_url(self):
        return reverse("aircraft_detail", args=[self.object.pk])

    def get_success_url(self):
        return reverse("aircraft_detail", args=[self.object.pk])


class TaskCreateView(EditorMixin, CreateView):
    model = MaintenanceTask
    form_class = TaskForm
    heading = "Add maintenance task"
    submit_label = "Add task"
    success_message = "Task added."
    success_url = reverse_lazy("task_list")

    def get_initial(self):
        initial = super().get_initial()
        aircraft_id = self.request.GET.get("aircraft", "")
        if aircraft_id.isdigit():
            initial["aircraft"] = int(aircraft_id)
        return initial

    def get_cancel_url(self):
        return reverse("task_list")


class TaskUpdateView(EditorMixin, UpdateView):
    model = MaintenanceTask
    form_class = TaskForm
    heading = "Edit task"
    submit_label = "Save changes"
    success_message = "Task updated."
    success_url = reverse_lazy("task_list")

    def get_cancel_url(self):
        return reverse("task_list")

    def get_delete_url(self):
        return reverse("task_delete", args=[self.object.pk])


class TaskDeleteView(LoginRequiredMixin, SuccessMessageMixin, DeleteView):
    login_url = "login"
    model = MaintenanceTask
    template_name = "tracker/confirm_delete.html"
    success_url = reverse_lazy("task_list")
    success_message = "Task deleted."


# ---------------------------------------------------------------------------
# Live aircraft map
# ---------------------------------------------------------------------------


def live_map(request):
    context = {
        "tile_url": settings.MAP_TILE_URL,
        "tile_attribution": settings.MAP_TILE_ATTRIBUTION,
        "data_attribution": settings.AIRCRAFT_ATTRIBUTION,
        "center_lat": 9.1,
        "center_lon": 8.0,
        "start_zoom": 7,
    }
    return render(request, "tracker/map.html", context)


def _fleet_rows():
    aircraft = Aircraft.objects.annotate(
        open_tasks=Count("tasks", filter=~Q(tasks__status=MaintenanceTask.Status.DONE))
    )
    return [
        {
            "id": item.pk,
            "registration": item.registration,
            "type": item.aircraft_type,
            "status": item.status,
            "status_label": item.get_status_display(),
            "open_tasks": item.open_tasks,
        }
        for item in aircraft
    ]


def aircraft_feed(request):
    """JSON for the live map: traffic in the visible area plus the whole fleet."""
    try:
        south, west, north, east = (
            float(request.GET[name]) for name in ("south", "west", "north", "east")
        )
    except (KeyError, ValueError):
        return JsonResponse(
            {"error": "bad_request", "message": "south, west, north and east must be numbers."},
            status=400,
        )
    if not all(math.isfinite(value) for value in (south, west, north, east)):
        return JsonResponse({"error": "bad_request", "message": "Invalid coordinates."}, status=400)

    south, north = max(-85.0, south), min(85.0, north)
    west, east = max(-179.999, west), min(179.999, east)
    if south >= north or west >= east:
        return JsonResponse({"error": "bad_request", "message": "Invalid map area."}, status=400)

    try:
        payload = live.get_feed((south, west, north, east), _fleet_rows())
    except live.TooManyCells:
        return JsonResponse(
            {"error": "zoom_in", "message": "Zoom in to see aircraft."}, status=422
        )
    except live.FeedError:
        return JsonResponse(
            {"error": "feed_unavailable", "message": "The live data feed is not reachable right now."},
            status=502,
        )
    response = JsonResponse(payload)
    response["Cache-Control"] = "public, max-age=5"
    return response
