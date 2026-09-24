from email.utils import getaddresses
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from apps.crm_email.automated import (
    FIELD_LABELS,
    OPTIONAL_FIELDS,
    RICH_FIELDS,
    AutomatedEmailSpec,
    tokens,
)
from apps.crm_email.security import clean_html, clean_rich_html, plain_text


class RecipientsField(forms.Field):
    def to_python(self, value: Any) -> list[str]:
        value = str(value or "").strip()
        if not value:
            return []
        if "\r" in value or "\n" in value:
            raise ValidationError("Enter email addresses separated by commas.")
        result = []
        for _, address in getaddresses([value]):
            validate_email(address)
            address = address.lower()
            if address not in result:
                result.append(address)
        if not result or len(result) > 30:
            raise ValidationError("Enter between 1 and 30 valid recipients.")
        return result


class ComposeForm(forms.Form):
    draft_id = forms.UUIDField(widget=forms.HiddenInput)
    reply_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    to = RecipientsField(label="To")
    cc = RecipientsField(label="CC", required=False)
    bcc = RecipientsField(label="BCC (visible only to you)", required=False)
    subject = forms.CharField(max_length=998)
    body_html = forms.CharField(
        label="Message", max_length=100000, widget=forms.Textarea(attrs={"rows": 10})
    )
    scheduled_at = forms.DateTimeField(
        label="Send later (Eastern time)",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    follow_up_days = forms.IntegerField(
        label="Create follow-up task after this many days",
        min_value=0,
        max_value=90,
        initial=0,
    )

    def clean_subject(self) -> str:
        subject: str = self.cleaned_data["subject"]
        if "\r" in subject or "\n" in subject:
            raise ValidationError("Subject must be a single line.")
        return subject

    def clean_body_html(self) -> str:
        value = clean_html(self.cleaned_data["body_html"])
        if not plain_text(value).strip():
            raise ValidationError("Enter a message.")
        return value

    def clean_scheduled_at(self) -> Any:
        value = self.cleaned_data["scheduled_at"]
        if value and value <= timezone.now():
            raise ValidationError("Choose a future send time.")
        return value


class TemplateForm(forms.Form):
    name = forms.CharField(max_length=120)
    subject = forms.CharField(max_length=998)
    body_html = forms.CharField(
        label="Message", max_length=100000, widget=forms.Textarea
    )

    def clean_subject(self) -> str:
        value: str = self.cleaned_data["subject"]
        if "\r" in value or "\n" in value:
            raise ValidationError("Subject must be a single line.")
        return value

    def clean_body_html(self) -> str:
        return clean_html(self.cleaned_data["body_html"])


class AutomatedEmailForm(forms.Form):
    """Edits the wording of one automated email; fields follow its registry spec."""

    def __init__(self, spec: AutomatedEmailSpec, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.spec = spec
        for name in spec.fields:
            rich = name in RICH_FIELDS
            self.fields[name] = forms.CharField(
                label=FIELD_LABELS[name],
                required=name not in OPTIONAL_FIELDS,
                max_length=200000 if rich else 998,
                widget=forms.Textarea(
                    attrs={
                        "rows": 12 if name == "body" else 4,
                        "data-rich-editor": "automated",
                    }
                )
                if rich
                else forms.TextInput(),
            )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        for name in self.spec.fields:
            value = cleaned.get(name)
            if value is None:
                continue
            if name in RICH_FIELDS:
                value = clean_rich_html(value)
                cleaned[name] = value
                if name not in OPTIONAL_FIELDS and not (
                    plain_text(value).strip() or "<img" in value
                ):
                    self.add_error(name, "Enter a message.")
                    continue
                if name in OPTIONAL_FIELDS and not (
                    plain_text(value).strip() or "<img" in value
                ):
                    cleaned[name] = ""
                    continue
            elif "\r" in value or "\n" in value:
                self.add_error(name, "Use a single line.")
            unknown = [
                token
                for token in tokens(plain_text(value) if name in RICH_FIELDS else value)
                if token not in self.spec.placeholders
            ]
            if unknown:
                self.add_error(
                    name,
                    "Unknown placeholder: "
                    + ", ".join("{{" + token + "}}" for token in dict.fromkeys(unknown))
                    + ". Use only the placeholders listed on this page.",
                )
            if (
                name == "action_url"
                and value
                and not (value.startswith("/") or value.startswith("https://"))
            ):
                self.add_error(
                    name,
                    "Enter a path on this site (starting with /) or an HTTPS link.",
                )
        return cleaned


class SignatureForm(forms.Form):
    signature = forms.CharField(
        max_length=10000, required=False, widget=forms.Textarea(attrs={"rows": 4})
    )

    def clean_signature(self) -> str:
        return clean_html(self.cleaned_data["signature"])


class NewsletterForm(forms.Form):
    """Compose a newsletter campaign with the rich editor."""

    subject = forms.CharField(max_length=255)
    preview_text = forms.CharField(
        max_length=255,
        required=False,
        label="Preview text",
        help_text="Shown next to the subject in most inboxes.",
    )
    body_html = forms.CharField(
        label="Newsletter",
        max_length=400000,
        widget=forms.Textarea(attrs={"rows": 16, "data-rich-editor": "automated"}),
    )

    def clean_subject(self) -> str:
        value: str = self.cleaned_data["subject"]
        if "\r" in value or "\n" in value:
            raise ValidationError("Subject must be a single line.")
        return value.strip()

    def clean_preview_text(self) -> str:
        value: str = self.cleaned_data["preview_text"]
        if "\r" in value or "\n" in value:
            raise ValidationError("Preview text must be a single line.")
        return value.strip()

    def clean_body_html(self) -> str:
        value = clean_rich_html(self.cleaned_data["body_html"])
        if not (plain_text(value).strip() or "<img" in value):
            raise ValidationError("Write the newsletter before saving.")
        return value
