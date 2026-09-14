from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms

from apps.crm.access import crm_owner_queryset
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
        min_value=10, max_value=120, initial=30, label="Duration in minutes"
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
