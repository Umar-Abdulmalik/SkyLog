from django.db import models
from django.utils import timezone


class Aircraft(models.Model):
    class Status(models.TextChoices):
        SERVICEABLE = "serviceable", "Serviceable"
        IN_MAINTENANCE = "maintenance", "In maintenance"
        GROUNDED = "grounded", "Grounded"

    registration = models.CharField(max_length=10, unique=True)
    aircraft_type = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SERVICEABLE
    )
    total_hours = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["registration"]
        verbose_name_plural = "aircraft"

    def __str__(self):
        return f"{self.registration} ({self.aircraft_type})"


class MaintenanceTask(models.Model):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        DONE = "done", "Done"

    aircraft = models.ForeignKey(
        Aircraft, on_delete=models.CASCADE, related_name="tasks"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.MEDIUM
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )
    due_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date"]

    def __str__(self):
        return f"{self.title} - {self.aircraft.registration}"

    @property
    def is_overdue(self):
        return self.status != self.Status.DONE and self.due_date < timezone.localdate()