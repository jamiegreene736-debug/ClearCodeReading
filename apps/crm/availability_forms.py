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

COMMON_TIMEZONES = (
    ("America/New_York", "Eastern — New York"),
    ("America/Chicago", "Central — Chicago"),
    ("America/Denver", "Mountain — Denver"),
    ("America/Phoenix", "Arizona — Phoenix"),
    ("America/Los_Angeles", "Pacific — Los Angeles"),
    ("America/Anchorage", "Alaska — Anchorage"),
    ("Pacific/Honolulu", "Hawaii — Honolulu"),
)


def timezone_choices() -> list[tuple[str, list[tuple[str, str]]]]:
    common_ids = {zone for zone, _label in COMMON_TIMEZONES}
    others = [
        (zone, zone.replace("_", " "))
        for zone in sorted(available_timezones())
        if zone not in common_ids
    ]
    return [("Common", list(COMMON_TIMEZONES)), ("All time zones", others)]


class BlockingTimezoneForm(forms.Form):
    blocking_timezone = forms.ChoiceField(
        label="Time zone",
        choices=timezone_choices(),
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
    mode = forms.ChoiceField(choices=BlockingRule.Mode.choices, label="This day")
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
        label="Date", widget=forms.DateInput(attrs={"type": "date"})
    )
    field_order = ("date", "mode", "starts_at", "ends_at")
