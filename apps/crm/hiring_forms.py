from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import forms
from django.core.validators import MaxLengthValidator

from apps.crm.hiring import CHECKLISTS, hiring_owner_queryset
from apps.crm.hiring_models import HiringCandidate
from apps.users.models import CustomUser

if TYPE_CHECKING:
    HiringModelForm = forms.ModelForm[HiringCandidate]
else:
    HiringModelForm = forms.ModelForm


class HiringUpdateForm(HiringModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    checklist = forms.MultipleChoiceField(
        choices=[item for group in CHECKLISTS.values() for item in group],
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = HiringCandidate
        fields = [
            "revision",
            "stage",
            "next_action",
            "due_date",
            "blocker",
            "checklist",
            "evaluation_notes",
            "decision",
            "decision_notes",
            "offer_sent_on",
            "offer_terms",
            "offer_response",
            "offer_responded_on",
            "onboarding_notes",
            "outcome_reason",
            "review_date",
        ]
        widgets = {
            field: forms.DateInput(attrs={"type": "date"})
            for field in [
                "due_date",
                "offer_sent_on",
                "offer_responded_on",
                "review_date",
            ]
        } | {
            field: forms.Textarea(attrs={"rows": 3, "maxlength": 10000})
            for field in [
                "blocker",
                "evaluation_notes",
                "decision_notes",
                "offer_terms",
                "onboarding_notes",
                "outcome_reason",
            ]
        }
        labels = {
            "next_action": "Next action",
            "due_date": "Due date",
            "blocker": "What's blocking progress?",
            "offer_terms": "Offer terms / document reference",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["next_action"].required = False
        for field in [
            "blocker",
            "evaluation_notes",
            "decision_notes",
            "offer_terms",
            "onboarding_notes",
            "outcome_reason",
        ]:
            self.fields[field].validators.append(MaxLengthValidator(10000))

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("stage") == "hold":
            cleaned["due_date"] = cleaned.get("review_date")
        return cleaned


class HiringOwnerForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    owner: forms.ModelChoiceField[CustomUser] = forms.ModelChoiceField(
        queryset=None, label="Owner for the entire hiring process", empty_label=None
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        owner_field = self.fields["owner"]
        assert isinstance(owner_field, forms.ModelChoiceField)
        owner_field.queryset = hiring_owner_queryset()
