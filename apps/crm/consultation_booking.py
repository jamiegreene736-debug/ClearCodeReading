"""Public consultation booking page for the default host's confirmed availability."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.conf import settings
from django.core.cache import cache
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from apps.crm.calendars import MAX_DAYS, available_slots
from apps.crm.consultations import default_consultation_host
from apps.crm.inventory_forms import SlotForm
from apps.crm.inventory_models import (
    ConsultationBooking,
    ConsultationSlot,
    InventoryBooking,
)
from apps.crm.models import CrmActivity, FormSubmission, Opportunity
from apps.crm.services import (
    LeadIntake,
    ensure_family_enrollment_deal,
    record_form_submission,
)
from apps.users.models import CustomUser

RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 3600


def consultation_booking_url() -> str:
    """Absolute HTTPS link to the public booking page, or "" when not public yet."""
    base = settings.PUBLIC_APP_URL.rstrip("/")
    if not base.startswith("https://"):
        return ""
    return base + reverse("consultation_booking")


class ConsultationBookingForm(forms.Form):
    name = forms.CharField(max_length=120, label="Your name")
    email = forms.EmailField(max_length=254, label="Email address")
    phone = forms.RegexField(
        r"^\+?[0-9 ()\-.]{7,32}$",
        max_length=32,
        label="Phone number",
        help_text="We call you at this number for the consultation.",
    )
    child_age_grade = forms.CharField(
        max_length=120, required=False, label="Child’s age or grade"
    )
    notes = forms.CharField(
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Anything you'd like us to know (optional)",
    )
    slot = forms.ChoiceField(label="Available appointment", widget=forms.RadioSelect)
    timezone = forms.CharField(
        initial="America/New_York", max_length=64, label="Your time zone"
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_timezone(self) -> str:
        return SlotForm.clean_timezone(self)

    def clean_email(self) -> str:
        value = self.cleaned_data["email"].strip().lower()
        validate_email(value)
        return value


def open_slots(host: CustomUser | None) -> list[ConsultationSlot]:
    if host is None:
        return []
    now = timezone.now()
    slots = ConsultationSlot.objects.filter(
        host=host,
        active=True,
        starts_at__gt=now,
        ends_at__lte=now + timedelta(days=MAX_DAYS),
        booking__isnull=True,
        consultation_booking__isnull=True,
        host__is_active=True,
        host__is_deleted=False,
    ).select_related("host")
    return available_slots(slots[:100])


def slot_label(slot: ConsultationSlot) -> str:
    return timezone.localtime(slot.starts_at).strftime("%A, %B %d · %I:%M %p %Z")


def client_key(request: HttpRequest) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    address = forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")
    return f"consultation-booking:{address or 'unknown'}"


@method_decorator(never_cache, name="dispatch")
class ConsultationBookingView(View):
    template_name = "crm/consultation_booking.html"

    def render_page(
        self,
        request: HttpRequest,
        form: ConsultationBookingForm | None = None,
        booking: ConsultationBooking | None = None,
        error: str = "",
    ) -> HttpResponse:
        host = default_consultation_host()
        slots = open_slots(host)
        form = form or ConsultationBookingForm()
        form.fields["slot"].choices = [
            (str(slot.pk), slot_label(slot)) for slot in slots
        ]
        response = render(
            request,
            self.template_name,
            {
                "form": form,
                "slots": slots,
                "host": host,
                "host_name": (host.get_full_name() if host else "") or "our team",
                "booking": booking,
                "error": error
                or (form.errors.get("slot", [""])[0] if form.is_bound else ""),
            },
        )
        response["Referrer-Policy"] = "same-origin"
        return response

    def get(self, request: HttpRequest) -> HttpResponse:
        booking = None
        token = request.GET.get("booked", "")
        if token.isdecimal():
            booking = (
                ConsultationBooking.objects.filter(pk=int(token))
                .select_related("slot__host", "lead")
                .first()
            )
            if booking and request.session.get("consultation_booking_id") != booking.pk:
                booking = None
        return self.render_page(request, booking=booking)

    def post(self, request: HttpRequest) -> HttpResponse:
        if request.POST.get("website", "").strip():
            return redirect("consultation_booking")
        key = client_key(request)
        attempts = cache.get(key, 0)
        if attempts >= RATE_LIMIT:
            return self.render_page(
                request, error="Too many booking attempts. Please try again later."
            )
        cache.set(key, attempts + 1, RATE_WINDOW_SECONDS)

        host = default_consultation_host()
        form = ConsultationBookingForm(request.POST)
        form.fields["slot"].choices = [
            (str(pk), str(pk))
            for pk in ConsultationSlot.objects.filter(
                host=host,
                active=True,
                starts_at__gt=timezone.now(),
                host__is_active=True,
                host__is_deleted=False,
            ).values_list("pk", flat=True)
        ]
        if not form.is_valid():
            return self.render_page(request, form)

        data = form.cleaned_data
        with transaction.atomic():
            slot_host = get_object_or_404(
                ConsultationSlot.objects.only("host_id"), pk=data["slot"]
            ).host_id
            CustomUser.objects.select_for_update().get(pk=slot_host)
            slot = get_object_or_404(
                # Same host-first lock order as the availability and assessment views.
                ConsultationSlot.objects.select_for_update(of=("self",)).select_related(
                    "host"
                ),
                pk=data["slot"],
            )
            if (
                not slot.active
                or slot.starts_at <= timezone.now()
                or slot.ends_at > timezone.now() + timedelta(days=MAX_DAYS)
                or not available_slots([slot])
                or InventoryBooking.objects.filter(slot=slot).exists()
                or ConsultationBooking.objects.filter(slot=slot).exists()
            ):
                form.add_error(
                    "slot", "That time is no longer available. Please choose another."
                )
                return self.render_page(request, form)

            appointment = slot.starts_at.astimezone(
                ZoneInfo(data["timezone"])
            ).strftime("%A, %B %d, %Y at %I:%M %p %Z")
            host_name = slot.host.get_full_name() or "ClearCode Reading"
            lead, submission = record_form_submission(
                intake=LeadIntake(
                    contact_email=data["email"],
                    contact_name=data["name"].strip(),
                    school_name="Family",
                    audience="family",
                    contact_phone=data["phone"],
                    notes=data["notes"],
                    metadata={"child_age_grade": data["child_age_grade"]},
                ),
                form_type=FormSubmission.FormType.CONSULTATION,
                source_path=reverse("consultation_booking"),
                submitted_data={
                    "name": data["name"].strip(),
                    "email": data["email"],
                    "phone": data["phone"],
                    "child_age_grade": data["child_age_grade"],
                    "notes": data["notes"],
                    "consultation_booked": True,
                    "consultation_time": appointment,
                    "consultation_host": host_name,
                    "consultation_slot_id": slot.pk,
                },
            )
            booking = ConsultationBooking.objects.create(
                lead=lead,
                submission=submission,
                slot=slot,
                phone=data["phone"],
                timezone=data["timezone"],
                child_age_grade=data["child_age_grade"],
                notes=data["notes"],
            )
            deal, _created = ensure_family_enrollment_deal(lead=lead, owner=slot.host)
            if deal.stage in {
                Opportunity.Stage.FAMILY_LEAD_NURTURE,
                Opportunity.Stage.FAMILY_WAITLIST,
            }:
                deal.stage = Opportunity.Stage.FAMILY_CONSULTATION
                deal.metadata = {
                    **(deal.metadata or {}),
                    "consultation_booking_id": booking.pk,
                }
                deal.save(update_fields=["stage", "metadata", "updated_at"])
            CrmActivity.objects.create(
                lead=lead,
                activity_type=CrmActivity.ActivityType.NOTE,
                subject="Consultation booked online",
                body=f"Booked for {appointment} with {host_name}. Deal: {deal.name}.",
            )
        request.session["consultation_booking_id"] = booking.pk
        return redirect(f"{reverse('consultation_booking')}?booked={booking.pk}")
