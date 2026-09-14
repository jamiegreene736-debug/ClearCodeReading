import ipaddress
import re
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.resources.access import can_publish, scoped_assets
from apps.resources.models import Revision, Topic

REVISION_FIELDS = (
    "title",
    "description",
    "kind",
    "audience",
    "topic",
    "body",
    "url",
    "asset",
    "cover",
    "cover_alt",
    "grade",
    "language",
    "access",
    "featured",
    "seo_title",
    "seo_description",
)


def validate_resource_url(value: str) -> str:
    if not value:
        return value
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or "." not in parsed.hostname
        or parsed.hostname.endswith((".local", ".localhost", ".internal"))
    ):
        raise ValidationError(
            "Paste a public HTTPS link, such as https://example.com/guide."
        )
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None:
        raise ValidationError("Use the website name instead of an IP address.")
    return value


def video_embed(value: str) -> str:
    parsed = urlparse(value)
    host = parsed.hostname or ""
    video_id = ""
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
        if parsed.path.startswith(("/shorts/", "/embed/")):
            video_id = parsed.path.split("/")[2]
    elif host == "youtu.be":
        video_id = parsed.path.strip("/")
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return f"https://www.youtube-nocookie.com/embed/{video_id}"
    if host in {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}:
        video_id = parsed.path.rstrip("/").split("/")[-1]
        if re.fullmatch(r"\d{1,12}", video_id):
            return f"https://player.vimeo.com/video/{video_id}"
    return ""


class ResourceForm(forms.ModelForm):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    upload = forms.FileField(required=False, label="Upload or replace file")
    cover_upload = forms.FileField(required=False, label="Cover image")
    remove_cover = forms.BooleanField(
        required=False, label="Use the automatic cover instead"
    )

    class Meta:
        model = Revision
        fields = REVISION_FIELDS
        labels: ClassVar = {
            "description": "Short description",
            "audience": "Who is it for?",
            "topic": "Topic",
            "body": "Your resource",
            "url": "Website or video link",
            "grade": "Grade range",
            "access": "Who can open this?",
            "featured": "Feature this resource",
            "asset": "Or reuse a file",
            "cover_alt": "Describe the cover image",
        }
        widgets: ClassVar = {
            "kind": forms.HiddenInput,
            "body": forms.Textarea(
                attrs={
                    "rows": 14,
                    "placeholder": "Write or paste your resource here. Use blank lines between paragraphs.",
                }
            ),
            "description": forms.Textarea(attrs={"rows": 3}),
            "cover": forms.HiddenInput,
        }

    def __init__(self, *args, user, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["asset"].queryset = scoped_assets(user)
        self.fields["asset"].label_from_instance = lambda asset: asset.name
        # The hidden retained cover is validated against the same owner boundary.
        self.fields["cover"].queryset = scoped_assets(user).filter(
            content_type__startswith="image/"
        )
        self.fields["topic"].queryset = Topic.objects.all()
        if not can_publish(user):
            self.fields.pop("access")
            self.fields.pop("featured")
        for field in ("title", "description", "body", "topic"):
            self.fields[field].required = False
        self.fields["upload"].widget.attrs["accept"] = (
            ".pdf,.docx,.pptx,.txt,.jpg,.jpeg,.png,.webp"
        )
        self.fields["cover_upload"].widget.attrs["accept"] = ".jpg,.jpeg,.png,.webp"

    def clean_url(self) -> str:
        try:
            return validate_resource_url(self.cleaned_data["url"])
        except ValueError as exc:
            raise ValidationError("Enter a valid HTTPS link.") from exc

    def clean_title(self) -> str:
        return self.cleaned_data.get("title", "").strip() or "Untitled resource"


def validate_publication(revision: Revision) -> None:
    errors = []
    if not revision.title.strip() or revision.title == "Untitled resource":
        errors.append("Add a title.")
    if not revision.description.strip():
        errors.append("Add a short description.")
    if not revision.topic_id:
        errors.append("Choose a topic.")
    if revision.kind == Revision.Kind.FILE and not revision.asset_id:
        errors.append("Upload or select a file.")
    if revision.kind == Revision.Kind.LINK and not revision.url:
        errors.append("Paste a link.")
    if revision.kind == Revision.Kind.ARTICLE and not revision.body.strip():
        errors.append("Write the resource content.")
    if revision.cover_id and not revision.cover_alt.strip():
        errors.append("Describe the cover image.")
    if errors:
        raise ValidationError(errors)


class ScheduleForm(forms.Form):
    publish_at = forms.DateTimeField(
        label="Publish at (UTC)",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
    )

    def clean_publish_at(self):
        value = self.cleaned_data["publish_at"]
        if value <= timezone.now():
            raise ValidationError("Choose a future date and time in UTC.")
        return value
