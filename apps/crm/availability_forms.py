"""Validated, accessible inputs for one weekly block per day and date exceptions."""

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django import forms

from apps.crm.calendar_models import BlockingRule

WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


class BlockingTimezoneForm(forms.Form):
    blocking_timezone = forms.ChoiceField(
        label="Time zone for weekly blocks and date overrides",
        choices=[
            (zone, zone.replace("_", " ")) for zone in sorted(available_timezones())
        ],
        initial="America/New_York",
    )

    def clean_blocking_timezone(self) -> str:
        value = str(self.cleaned_data["blocking_timezone"])
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise forms.ValidationError("Choose a valid time zone.") from exc
        return value


class BlockingRuleForm(forms.Form):
    mode = forms.ChoiceField(choices=BlockingRule.Mode.choices, label="Blocking rule")
    starts_at = forms.TimeField(
        required=False,
        label="From",
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    ends_at = forms.TimeField(
        required=False,
        label="To",
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("mode") == "range":
            for field in ("starts_at", "ends_at"):
                if not data.get(field):
                    self.add_error(field, "Enter a time for this block.")
            if data.get("starts_at") and data.get("starts_at") == data.get("ends_at"):
                self.add_error(
                    "ends_at", "Choose a different end time, or use Block all day."
                )
        else:
            data["starts_at"] = data["ends_at"] = None
        return data


class DateOverrideForm(BlockingRuleForm):
    date = forms.DateField(
        label="Date to override", widget=forms.DateInput(attrs={"type": "date"})
    )
    field_order = ("date", "mode", "starts_at", "ends_at")
