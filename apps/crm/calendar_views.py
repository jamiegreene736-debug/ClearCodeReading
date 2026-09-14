"""Self-service calendar connections and revocable, minimal booking subscriptions."""

import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET
from icalendar import Calendar, Event

from apps.crm import google_calendar
from apps.crm.calendar_models import HostCalendar
from apps.crm.calendars import (
    MAX_DAYS,
    CalendarError,
    busy_periods,
    check_calendar,
    cipher,
    fetch_calendar,
    normalize_url,
)
from apps.crm.inventory_models import InventoryBooking
from apps.crm.views import CrmAccessMixin
from apps.users.models import CustomUser


class CalendarRequest(HttpRequest):
    user: CustomUser


class CalendarForm(forms.Form):
    calendar_url = forms.CharField(
        label="Calendar sharing link",
        max_length=2000,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
    )
    source_timezone = forms.CharField(
        label="Calendar time zone", max_length=64, initial="America/New_York"
    )

    def clean_calendar_url(self) -> str:
        try:
            return normalize_url(self.cleaned_data["calendar_url"])
        except CalendarError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean_source_timezone(self) -> str:
        value = str(self.cleaned_data["source_timezone"]).strip()
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise forms.ValidationError(
                "Use a time zone such as America/New_York."
            ) from exc
        return value


class CalendarSettingsView(CrmAccessMixin, View):
    def get(self, request: CalendarRequest) -> HttpResponse:
        return self.page(request, CalendarForm())

    def page(self, request: CalendarRequest, form: CalendarForm) -> HttpResponse:
        profile = HostCalendar.objects.filter(host=request.user).first()
        feed = (
            request.build_absolute_uri(
                reverse("crm_calendar_feed", args=[profile.subscription_token])
            )
            if profile
            else ""
        )
        response = render(
            request,
            "crm/calendar_settings.html",
            {
                "profile": profile,
                "google_configured": google_calendar.configured(),
                "form": form,
                "feed_url": feed,
                "webcal_url": feed.replace("https://", "webcal://").replace(
                    "http://", "webcal://"
                ),
            },
        )
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response

    def post(self, request: CalendarRequest) -> HttpResponse:
        action = request.POST.get("action", "connect")
        form = CalendarForm(request.POST)
        if action == "connect":
            if form.is_valid():
                try:
                    url = form.cleaned_data["calendar_url"]
                    zone = form.cleaned_data["source_timezone"]
                    now = timezone.now()
                    busy_periods(
                        fetch_calendar(url), now, now + timedelta(days=MAX_DAYS), zone
                    )
                    with transaction.atomic():
                        CustomUser.objects.select_for_update().get(pk=request.user.pk)
                        HostCalendar.objects.update_or_create(
                            host=request.user,
                            defaults={
                                "google_email": "",
                                "encrypted_google_refresh_token": "",
                                "encrypted_url": cipher()
                                .encrypt(url.encode())
                                .decode(),
                                "source_timezone": zone,
                                "last_checked_at": now,
                                "last_error": "",
                            },
                        )
                    messages.success(
                        request,
                        "Calendar connected. Busy times will be checked before consultations are offered or booked.",
                    )
                    return redirect("crm_calendar_settings")
                except CalendarError as exc:
                    form.add_error(None, str(exc))
            return self.page(request, form)
        if action not in {"disconnect", "rotate", "check", "subscribe"}:
            return HttpResponse(status=400)
        with transaction.atomic():
            CustomUser.objects.select_for_update().get(pk=request.user.pk)
            profile, _ = HostCalendar.objects.get_or_create(host=request.user)
            if action == "disconnect":
                profile.encrypted_url = ""
                profile.encrypted_google_refresh_token = ""
                profile.google_email = ""
                profile.last_error = ""
                profile.last_checked_at = None
                profile.save()
                messages.success(
                    request,
                    "Calendar disconnected. Only your manually confirmed availability will be used.",
                )
            elif action == "rotate":
                profile.subscription_token = uuid.uuid4()
                profile.save(update_fields=["subscription_token"])
                messages.success(
                    request,
                    "Subscription link replaced. Add the new link to your calendar; the previous link no longer works.",
                )
            elif action == "check" and (
                profile.encrypted_url or profile.encrypted_google_refresh_token
            ):
                try:
                    now = timezone.now()
                    check_calendar(profile, now, now + timedelta(days=MAX_DAYS))
                    messages.success(request, "Calendar checked successfully.")
                except CalendarError as exc:
                    messages.error(request, str(exc))
        return redirect("crm_calendar_settings")


@require_GET
def calendar_feed(request: CalendarRequest, token: uuid.UUID) -> HttpResponse:
    profile = get_object_or_404(
        HostCalendar,
        subscription_token=token,
        host__is_active=True,
        host__is_deleted=False,
    )
    if not profile.host.has_crm_access:
        return HttpResponse(status=404)
    calendar = Calendar()
    calendar.add("prodid", "-//ClearCode Reading//Consultations//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", "ClearCode consultations")
    for booking in InventoryBooking.objects.filter(
        slot__host_id=profile.host_id,
        slot__ends_at__gte=timezone.now() - timedelta(days=30),
    ).select_related("slot"):
        event = Event()
        event.add("uid", f"inventory-booking-{booking.pk}@clearcodereading.com")
        event.add("dtstamp", booking.created_at)
        event.add("dtstart", booking.slot.starts_at)
        event.add("dtend", booking.slot.ends_at)
        event.add("summary", "ClearCode reading consultation")
        event.add(
            "description",
            "Phone consultation. Sign in to ClearCode CRM for contact details.",
        )
        event.add("status", "CONFIRMED")
        calendar.add_component(event)
    response = HttpResponse(
        calendar.to_ical(), content_type="text/calendar; charset=utf-8"
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response


class GoogleCalendarConnectView(CrmAccessMixin, View):
    def post(self, request: CalendarRequest) -> HttpResponse:
        try:
            return redirect(google_calendar.authorization_url(request))
        except CalendarError as exc:
            messages.error(request, str(exc))
            return redirect("crm_calendar_settings")


def google_calendar_callback(request: HttpRequest) -> HttpResponse:
    # Access is checked by the registered Google callback before dispatch here.
    try:
        google_calendar.connect(request)
        messages.success(
            request,
            "Google Calendar connected. Your primary calendar’s busy times will now be checked.",
        )
    except CalendarError as exc:
        messages.error(request, str(exc))
    response = redirect("crm_calendar_settings")
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
