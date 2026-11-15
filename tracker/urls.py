from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("aircraft/", views.aircraft_list, name="aircraft_list"),
    path("aircraft/new/", views.AircraftCreateView.as_view(), name="aircraft_create"),
    path("aircraft/<int:pk>/", views.aircraft_detail, name="aircraft_detail"),
    path("aircraft/<int:pk>/edit/", views.AircraftUpdateView.as_view(), name="aircraft_edit"),
    path("map/", views.live_map, name="live_map"),
    path("api/aircraft/", views.aircraft_feed, name="aircraft_feed"),
    path("tasks/", views.task_list, name="task_list"),
    path("tasks/new/", views.TaskCreateView.as_view(), name="task_create"),
    path("tasks/<int:pk>/edit/", views.TaskUpdateView.as_view(), name="task_edit"),
    path("tasks/<int:pk>/delete/", views.TaskDeleteView.as_view(), name="task_delete"),
    path("login/", views.SkyLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="home"), name="logout"),
]
