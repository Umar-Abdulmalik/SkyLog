from django.contrib import admin

from .models import Aircraft, MaintenanceTask


@admin.register(Aircraft)
class AircraftAdmin(admin.ModelAdmin):
    list_display = ("registration", "aircraft_type", "status", "total_hours")
    list_filter = ("status",)
    search_fields = ("registration", "aircraft_type")


@admin.register(MaintenanceTask)
class MaintenanceTaskAdmin(admin.ModelAdmin):
    list_display = ("title", "aircraft", "priority", "status", "due_date")
    list_filter = ("status", "priority")
    search_fields = ("title", "aircraft__registration")