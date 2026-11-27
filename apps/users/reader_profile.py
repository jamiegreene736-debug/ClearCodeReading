"""Private, guardian-managed details for an individual reader."""

from datetime import date
from io import BytesIO

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.users.family import family_relationship
from apps.users.models import AuditLog, ChildProfile, ReaderDetails, SignedReaderForms
from apps.users.onboarding_views import PortalRequest


PHOTO_MAX_MB = 50
PHOTO_MAX_MEGAPIXELS = 50
PHOTO_MAX_BYTES = PHOTO_MAX_MB * 1024 * 1024
PHOTO_MAX_PIXELS = PHOTO_MAX_MEGAPIXELS * 1_000_000


class ReaderProfileForm(forms.Form):
    first_name = forms.CharField(max_length=120)
    last_name = forms.CharField(max_length=120, required=False)
    school_name = forms.CharField(label="School", max_length=200, required=False)
    grade_level = forms.ChoiceField(
        label="Grade",
        choices=[("", "Choose a grade"), *ChildProfile.GradeLevel.choices],
        required=False,
    )
    date_of_birth = forms.DateField(
        label="Birthday",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        required=False,
    )
    photo = forms.FileField(
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )
    remove_photo = forms.BooleanField(label="Remove saved photo", required=False)
    activities = forms.CharField(
        label="What does your reader enjoy doing?",
        max_length=500,
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "placeholder": "Playing soccer, drawing, cooking with family…",
            }
        ),
    )
    favorites = forms.CharField(
        label="Favorite characters, books or topics",
        max_length=500,
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "placeholder": "Hello Kitty, dinosaurs, space adventures…",
            }
        ),
    )

    def clean_date_of_birth(self) -> date | None:
        birthday = self.cleaned_data.get("date_of_birth")
        if birthday and birthday > timezone.localdate():
            raise forms.ValidationError("Choose today or an earlier birthday.")
        return birthday

    def clean_photo(self) -> bytes | None:
        upload = self.cleaned_data.get("photo")
        if not upload:
            return None
        if upload.size > PHOTO_MAX_BYTES:
            raise forms.ValidationError(f"Choose a photo up to {PHOTO_MAX_MB} MB.")
        try:
            with Image.open(upload, formats=["JPEG", "PNG", "WEBP"]) as source:
                if source.width * source.height > PHOTO_MAX_PIXELS:
                    raise forms.ValidationError(
                        f"Choose a photo up to {PHOTO_MAX_MEGAPIXELS} megapixels."
                    )
                source.draft("RGB", (512, 512))
                source.thumbnail((512, 512))
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.info.clear()
                output = BytesIO()
                image.save(output, "JPEG", quality=85)
                return output.getvalue()
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
        ) as exc:
            raise forms.ValidationError(
                "Choose a valid JPG, PNG or WebP photo."
            ) from exc

    @transaction.atomic
    def save_reader(self, request: PortalRequest, child_id: int) -> None:
        relationship = family_relationship(request.user, child_id, lock=True)
        if not SignedReaderForms.objects.filter(relationship=relationship).exists():
            raise Http404
        child = relationship.child
        data = self.cleaned_data
        for field in ("first_name", "last_name", "date_of_birth", "grade_level"):
            setattr(child, field, data[field])
        child.save(
            update_fields=[
                "first_name",
                "last_name",
                "date_of_birth",
                "grade_level",
                "updated_at",
            ]
        )
        values = {key: data[key] for key in ("school_name", "activities", "favorites")}
        if data["photo"] is not None or data["remove_photo"]:
            values["photo"] = data["photo"] or b""
        ReaderDetails.objects.update_or_create(child=child, defaults=values)
        AuditLog.objects.create(
            actor=request.user,
            action="reader.profile_saved",
            entity_type="ChildProfile",
            entity_id=str(child_id),
        )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def reader_profile(request: PortalRequest, child_id: int) -> HttpResponse:
    relationship = family_relationship(request.user, child_id)
    if not SignedReaderForms.objects.filter(relationship=relationship).exists():
        return redirect("portal_reader_forms", child_id=child_id)
    child = relationship.child
    details = ReaderDetails.objects.filter(child=child).defer("photo").first()
    has_photo = ReaderDetails.objects.filter(child=child).exclude(photo=b"").exists()
    initial = {
        key: getattr(child, key)
        for key in ("first_name", "last_name", "date_of_birth", "grade_level")
    }
    if details:
        initial.update(
            {
                key: getattr(details, key)
                for key in ("school_name", "activities", "favorites")
            }
        )
    form = ReaderProfileForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        initial=initial,
    )
    if form.is_bound and form.is_valid():
        form.save_reader(request, child_id)
        messages.success(request, "Your Reader Profile is saved.")
        return redirect("portal_dashboard")
    return render(
        request,
        "portal/reader_profile.html",
        {
            "form": form,
            "child": child,
            "has_photo": has_photo,
            "photo_max_mb": PHOTO_MAX_MB,
            "photo_max_megapixels": PHOTO_MAX_MEGAPIXELS,
            "today": timezone.localdate(),
        },
    )


@login_required
@never_cache
@require_GET
def reader_photo(request: PortalRequest, child_id: int) -> HttpResponse:
    relationship = family_relationship(request.user, child_id)
    if not SignedReaderForms.objects.filter(relationship=relationship).exists():
        raise Http404
    photo = (
        ReaderDetails.objects.filter(child_id=child_id)
        .values_list("photo", flat=True)
        .first()
    )
    if not photo:
        raise Http404
    return HttpResponse(bytes(photo), content_type="image/jpeg")
