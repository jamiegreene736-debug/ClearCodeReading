"""Calendar subscriptions: private URLs, bounded reads and fail-closed availability."""

import base64
import hashlib
import re
import time as monotonic_time
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from icalendar import Calendar

from apps.crm.calendar_models import HostCalendar
from apps.crm.inventory_models import ConsultationSlot

MAX_BYTES = 2 * 1024 * 1024
MAX_DAYS = 90


class CalendarError(Exception):
    """Safe error text that never includes a private calendar URL or event details."""


def cipher() -> MultiFernet:
    # Domain-separated from other encrypted data; Django fallback keys permit rotation.
    return MultiFernet(
        [
            Fernet(
                base64.urlsafe_b64encode(
                    hashlib.sha256(
                        b"clearcode-calendar-v1:" + force_bytes(key)
                    ).digest()
                )
            )
            for key in [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
        ]
    )


def normalize_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname or ""
        allowed = (
            host == "calendar.google.com" and parts.path.startswith("/calendar/ical/")
        ) or (
            bool(re.fullmatch(r"p\d+-calendars\.icloud\.com", host))
            and parts.path.startswith("/published/")
        )
        if (
            parts.scheme not in {"https", "webcal"}
            or not allowed
            or parts.username
            or parts.password
            or parts.port not in {None, 443}
            or parts.fragment
            or len(value) > 2000
        ):
            raise ValueError
        return urlunsplit(("https", host, parts.path, parts.query, ""))
    except ValueError as exc:
        raise CalendarError(
            "Use a Google Calendar iCal link or an iCloud published calendar link."
        ) from exc


def fetch_calendar(url: str) -> bytes:
    try:
        # Fixed provider domains and no redirects prevent access to arbitrary servers.
        with requests.get(
            normalize_url(url), timeout=(5, 10), stream=True, allow_redirects=False
        ) as response:
            if response.status_code != 200:
                raise CalendarError(
                    "Calendar could not be read. Check the sharing link and try again."
                )
            payload = bytearray()
            started = monotonic_time.monotonic()
            for chunk in response.iter_content(65536):
                payload.extend(chunk)
                if monotonic_time.monotonic() - started > 15:
                    raise CalendarError(
                        "Calendar download timed out. Please try again."
                    )
                if len(payload) > MAX_BYTES:
                    raise CalendarError(
                        "Calendar is too large. Share a smaller calendar."
                    )
            return bytes(payload)
    except requests.RequestException as exc:
        raise CalendarError(
            "Calendar is temporarily unavailable. Please try again."
        ) from exc


def as_datetime(value: date | datetime, zone: ZoneInfo) -> datetime:
    if not isinstance(value, datetime):
        return datetime.combine(value, time.min, zone)
    return value.replace(tzinfo=zone) if timezone.is_naive(value) else value


def busy_periods(
    payload: bytes, start: datetime, end: datetime, source_timezone: str
) -> list[tuple[datetime, datetime]]:
    try:
        calendar = Calendar.from_ical(payload)
        if calendar.name != "VCALENDAR" or calendar.errors:
            raise ValueError
        zone = ZoneInfo(source_timezone)
        events = calendar.walk("VEVENT")
        if len(events) > 2000:
            raise ValueError
        for event in events:
            if event.errors or "DTSTART" not in event:
                raise ValueError
            # Subdaily recurrences can expand into millions of records. Reject,
            # rather than silently ignoring, unsupported high-frequency calendars.
            rule = event.get("RRULE")
            if rule and any(
                len(rule.get(key, [])) > 1 for key in ("BYHOUR", "BYMINUTE", "BYSECOND")
            ):
                raise ValueError
            if rule and str(rule.get("FREQ", [""])[0]) not in {
                "DAILY",
                "WEEKLY",
                "MONTHLY",
                "YEARLY",
            }:
                raise ValueError
        busy = []
        for event in recurring_ical_events.of(calendar).between(
            start - timedelta(days=2), end + timedelta(days=2)
        ):
            if (
                str(event.get("STATUS", "")).upper() == "CANCELLED"
                or str(event.get("TRANSP", "")).upper() == "TRANSPARENT"
            ):
                continue
            begins = as_datetime(event.decoded("DTSTART"), zone)
            ends = as_datetime(event.decoded("DTEND"), zone)
            if ends < begins:
                raise ValueError
            if begins < end and ends > start:
                busy.append((begins, ends))
        return busy
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise CalendarError(
            "Calendar format could not be checked safely. Verify the calendar and time zone."
        ) from exc


def check_calendar(
    profile: HostCalendar, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    try:
        url = cipher().decrypt(profile.encrypted_url.encode()).decode()
        periods = busy_periods(fetch_calendar(url), start, end, profile.source_timezone)
    except InvalidToken as exc:
        error = CalendarError("Reconnect your calendar to restore availability checks.")
        HostCalendar.objects.filter(pk=profile.pk).update(last_error=str(error))
        raise error from exc
    except CalendarError as exc:
        HostCalendar.objects.filter(pk=profile.pk).update(last_error=str(exc))
        raise
    HostCalendar.objects.filter(pk=profile.pk).update(
        last_checked_at=timezone.now(), last_error=""
    )
    return periods


def available_slots(slots: Iterable[ConsultationSlot]) -> list[ConsultationSlot]:
    candidates = list(slots)
    if not candidates:
        return []
    profiles = {
        profile.host_id: profile
        for profile in HostCalendar.objects.filter(
            host_id__in={slot.host_id for slot in candidates}
        ).exclude(encrypted_url="")
    }
    blocked: set[int] = set()
    periods_by_host: dict[int, list[tuple[datetime, datetime]]] = {}
    for host_id, profile in profiles.items():
        owned = [slot for slot in candidates if slot.host_id == host_id]
        start = min(slot.starts_at for slot in owned)
        end = max(slot.ends_at for slot in owned)
        if end - start > timedelta(days=MAX_DAYS):
            blocked.add(host_id)
            continue
        try:
            periods_by_host[host_id] = check_calendar(profile, start, end)
        except CalendarError:
            # Never turn a failed provider read into apparently free time.
            blocked.add(host_id)
    return [
        slot
        for slot in candidates
        if slot.host_id not in blocked
        and not any(
            begins < slot.ends_at and ends > slot.starts_at
            for begins, ends in periods_by_host.get(slot.host_id, [])
        )
    ]
