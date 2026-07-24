from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class RemoteBooking:
    external_booking_id: str
    starts_at: datetime
    ends_at: datetime
    canceled: bool
    raw: dict


class SchedulerAdapter(Protocol):
    """Provider-neutral contract for Jane App, Acuity, or another bought scheduler."""

    provider: str

    def upsert_booking(self, booking) -> str:
        """Create or update an approved local booking and return its remote ID."""
        ...

    def pull_bookings(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        updated_since: datetime | None = None,
    ) -> list[RemoteBooking]:
        """Return normalized remote changes for existing local bookings."""
        ...


class SchedulerError(RuntimeError):
    pass


class SchedulerNotConfigured(SchedulerError):
    pass


class StubSchedulerAdapter:
    """Deterministic, no-network adapter for development and contract tests."""

    provider = "stub"

    def upsert_booking(self, booking) -> str:
        return booking.external_booking_id or f"stub-{booking.pk}"

    def pull_bookings(self, **kwargs) -> list[RemoteBooking]:
        return []


class AcuitySchedulerAdapter:
    """Acuity Scheduling adapter using the documented admin appointment API."""

    provider = "acuity"
    base_url = "https://acuityscheduling.com/api/v1"

    def __init__(self):
        self.user_id = getattr(settings, "ACUITY_USER_ID", "")
        self.api_key = getattr(settings, "ACUITY_API_KEY", "")
        self.appointment_type_id = getattr(settings, "ACUITY_APPOINTMENT_TYPE_ID", "")
        self.calendar_ids = getattr(settings, "ACUITY_CALENDAR_IDS", {})
        if not self.user_id or not self.api_key or not self.appointment_type_id:
            raise SchedulerNotConfigured(
                "Acuity sync requires ACUITY_USER_ID, ACUITY_API_KEY, and ACUITY_APPOINTMENT_TYPE_ID."
            )

    def _request(self, method: str, path: str, *, query: dict | None = None, payload: dict | None = None):
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        token = base64.b64encode(f"{self.user_id}:{self.api_key}".encode("utf-8")).decode("ascii")
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = "external scheduler rejected the request"
            try:
                response = json.loads(error.read().decode("utf-8"))
                detail = response.get("message") or response.get("error") or detail
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise SchedulerError(f"Acuity returned HTTP {error.code}: {detail}.") from error
        except (URLError, TimeoutError) as error:
            raise SchedulerError("Acuity could not be reached; retry from the operations queue.") from error

    def _calendar_id(self, booking):
        explicit = booking.metadata.get("acuity_calendar_id")
        if explicit:
            return explicit
        return self.calendar_ids.get(str(booking.specialist_id)) or self.calendar_ids.get(booking.specialist.email)

    def upsert_booking(self, booking) -> str:
        calendar_id = self._calendar_id(booking)
        if not calendar_id:
            raise SchedulerNotConfigured(
                f"No Acuity calendar is mapped to specialist {booking.specialist_id}."
            )
        appointment_datetime = booking.starts_at.isoformat()
        if booking.external_booking_id:
            self._request(
                "PUT",
                f"/appointments/{booking.external_booking_id}/reschedule",
                query={"admin": "true", "noEmail": "true"},
                payload={"datetime": appointment_datetime, "calendarID": calendar_id},
            )
            return booking.external_booking_id

        child_email = getattr(booking.child.user, "email", "") if booking.child.user_id else ""
        payload = {
            "datetime": appointment_datetime,
            "appointmentTypeID": self.appointment_type_id,
            "calendarID": calendar_id,
            "firstName": booking.child.first_name,
            "lastName": booking.child.last_name or "-",
            "email": child_email or booking.center.contact_email,
            "notes": f"ClearCode ScheduleBooking {booking.pk}",
        }
        result = self._request(
            "POST",
            "/appointments",
            query={"admin": "true", "noEmail": "true"},
            payload=payload,
        )
        external_id = result.get("id")
        if external_id is None:
            raise SchedulerError("Acuity did not return an appointment ID.")
        return str(external_id)

    def pull_bookings(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        updated_since: datetime | None = None,
    ) -> list[RemoteBooking]:
        query: dict = {"showall": "true", "max": 100}
        if start_date:
            query["minDate"] = start_date.isoformat()
        elif updated_since:
            query["minDate"] = updated_since.date().isoformat()
        if end_date:
            query["maxDate"] = end_date.isoformat()
        appointments = self._request("GET", "/appointments", query=query)
        normalized = []
        for appointment in appointments:
            starts_at = parse_datetime(str(appointment.get("datetime", "")))
            if starts_at is None:
                continue
            try:
                duration = int(appointment.get("duration", 0))
            except (TypeError, ValueError):
                continue
            if duration <= 0:
                continue
            normalized.append(
                RemoteBooking(
                    external_booking_id=str(appointment["id"]),
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(minutes=duration),
                    canceled=bool(appointment.get("canceled")),
                    raw=appointment,
                )
            )
        return normalized


def get_scheduler_adapter() -> SchedulerAdapter:
    adapter_path = getattr(settings, "SCHEDULER_ADAPTER", "")
    if not adapter_path:
        raise SchedulerNotConfigured("Configure Jane App or Acuity before syncing approved schedules.")
    return import_string(adapter_path)()
