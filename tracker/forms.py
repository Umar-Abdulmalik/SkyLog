from django import forms

from .models import Aircraft, MaintenanceTask


class AircraftForm(forms.ModelForm):
    class Meta:
        model = Aircraft
        fields = ["registration", "aircraft_type", "status", "total_hours"]
        labels = {"aircraft_type": "Aircraft type", "total_hours": "Total flight hours"}

    def clean_registration(self):
        return self.cleaned_data["registration"].strip().upper()


class TaskForm(forms.ModelForm):
    class Meta:
        model = MaintenanceTask
        fields = ["aircraft", "title", "description", "priority", "status", "due_date"]
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {"due_date": "Due date"}
