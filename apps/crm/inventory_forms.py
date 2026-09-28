from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.utils import timezone

from apps.crm.access import crm_owner_queryset
from apps.crm.consultations import default_consultation_host, editable_hosts
from apps.crm.inventory import GRADES, definition
from apps.crm.inventory_models import InventoryChild


class InvitationForm(forms.Form):
    child = forms.ModelChoiceField(
        queryset=InventoryChild.objects.none(),
        required=False,
        empty_label="Add a child",
    )
    child_name = forms.CharField(max_length=120, required=False)
    grade = forms.ChoiceField(
        choices=[(key, value["label"]) for key, value in GRADES.items()]
    )
    home_zip = forms.RegexField(r"^\d{5}(?:-\d{4})?$", required=False, label="ZIP code")
    recipient = forms.EmailField()
    subject = forms.CharField(
        max_length=200, initial="Your ClearCode Parent Reading Inventory"
    )
    message = forms.CharField(max_length=5000, widget=forms.Textarea)

    def __init__(self, *args, parent, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = parent.inventory_children.all()

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("child") and not cleaned.get("child_name"):
            self.add_error(
                "child_name", "Enter a child name or select an existing child."
            )
        if "\n" in cleaned.get("subject", "") or "\r" in cleaned.get("subject", ""):
            self.add_error("subject", "Use one line for the subject.")
        if cleaned.get("child") and cleaned["child"].grade != cleaned.get("grade"):
            self.add_error(
                "grade",
                "Select the existing child’s grade. Create a new assessment child record for a different grade.",
            )
        return cleaned


class SectionForm(forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, grade, group_index, answers, revision, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["revision"].initial = revision
        for question in definition(grade)["groups"][group_index]["questions"]:
            self.fields[question["id"]] = forms.ChoiceField(
                label=question["prompt"],
                choices=[("yes", "Yes"), ("no", "No")],
                widget=forms.RadioSelect,
                required=False,
                help_text=" ".join(question["examples"]),
                initial=("yes" if answers[question["id"]] else "no")
                if question["id"] in answers
                else None,
            )


class SlotForm(forms.Form):
    host = forms.ModelChoiceField(queryset=crm_owner_queryset())
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    timezone = forms.CharField(
        initial="America/New_York",
        max_length=64,
        label="Time zone (e.g. America/New_York)",
    )
    duration = forms.IntegerField(
        min_value=10,
        max_value=120,
        initial=15,
        label="Duration in minutes",
        help_text="Signup times from open hours are 15 minutes. Use this for one extra time.",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            hosts = editable_hosts(user)
            self.fields["host"].queryset = hosts
            default = default_consultation_host()
            self.fields["host"].initial = (
                default if default and hosts.filter(pk=default.pk).exists() else user
            )

    def clean_timezone(self):
        value = self.cleaned_data["timezone"]
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise forms.ValidationError(
                "Enter a valid time zone, such as America/New_York."
            ) from exc
        return value


class OpenWindowForm(forms.Form):
    """A same-day range that becomes bookable 15-minute signup times."""

    host = forms.ModelChoiceField(queryset=crm_owner_queryset())
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    starts_at = forms.TimeField(
        initial="15:00",
        label="From",
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    ends_at = forms.TimeField(
        initial="17:00",
        label="To",
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    timezone = forms.CharField(
        initial="America/New_York",
        max_length=64,
        label="Time zone (e.g. America/New_York)",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            hosts = editable_hosts(user)
            self.fields["host"].queryset = hosts
            default = default_consultation_host()
            self.fields["host"].initial = (
                default if default and hosts.filter(pk=default.pk).exists() else user
            )

    def clean_timezone(self):
        return SlotForm.clean_timezone(self)

    def clean(self):
        cleaned = super().clean() or {}
        starts = cleaned.get("starts_at")
        ends = cleaned.get("ends_at")
        day = cleaned.get("date")
        zone_name = cleaned.get("timezone")
        if not starts or not ends or not day or not zone_name:
            return cleaned
        for field, value in (("starts_at", starts), ("ends_at", ends)):
            if value.second or value.microsecond or value.minute % 15:
                self.add_error(
                    field, "Use a 15-minute increment, such as 3:00 or 3:15."
                )
        if starts and ends and ends <= starts:
            self.add_error("ends_at", "Choose an end time after the start time.")
            return cleaned
        if self.errors:
            return cleaned
        zone = ZoneInfo(zone_name)
        start = datetime.combine(day, starts, zone)
        end = datetime.combine(day, ends, zone)
        if start.fold == 0 and start.utcoffset() != start.replace(fold=1).utcoffset():
            self.add_error(
                "starts_at", "Choose a time outside the daylight-saving clock change."
            )
        elif (end - start) < timedelta(minutes=15):
            self.add_error("ends_at", "Leave at least 15 minutes for one signup time.")
        elif start <= timezone.now():
            self.add_error("date", "Choose a future block of hours.")
        return cleaned


class BookingForm(forms.Form):
    slot = forms.ChoiceField(label="Available appointment")
    phone = forms.RegexField(
        r"^\+?[0-9 ()\-.]{7,32}$", max_length=32, label="Phone number"
    )
    timezone = forms.CharField(
        initial="America/New_York", max_length=64, label="Your time zone"
    )

    def clean_timezone(self):
        return SlotForm.clean_timezone(self)
