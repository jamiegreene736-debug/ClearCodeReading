"""Turn a host's open hours into 15-minute consultation times families can book."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.crm.calendars import MAX_DAYS
from apps.crm.inventory_models import (
    ConsultationHoursSeed,
    ConsultationOpenWindow,
    ConsultationSlot,
)
from apps.users.models import CustomUser

CONSULTATION_MINUTES = 15
PUBLISHED_EMAIL = "bethany@clearcodereading.com"
PUBLISHED_KEY = "bethany-open-2026-09-28"
PUBLISHED_TIMEZONE = "America/New_York"
# Week of Monday, September 28, 2026: Monday–Friday.
# The following week: Monday–Wednesday. Each day is 3:00–5:00 p.m. Eastern.
PUBLISHED_DATES = (
    date(2026, 9, 28),
    date(2026, 9, 29),
    date(2026, 9, 30),
    date(2026, 10, 1),
    date(2026, 10, 2),
    date(2026, 10, 5),
    date(2026, 10, 6),
    date(2026, 10, 7),
)
PUBLISHED_START = time(15, 0)
PUBLISHED_END = time(17, 0)


def iter_window_slots(
    day: date, starts_at: time, ends_at: time, zone_name: str
) -> list[tuple[datetime, datetime]]:
    """15-minute appointments that begin on a quarter hour and finish by ends_at."""
    zone = ZoneInfo(zone_name)
    start = datetime.combine(day, starts_at, zone)
    end = datetime.combine(day, ends_at, zone)
    if start.fold == 0 and start.utcoffset() != start.replace(fold=1).utcoffset():
        return []
    step = timedelta(minutes=CONSULTATION_MINUTES)
    slots: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor + step <= end:
        slots.append((cursor, cursor + step))
        cursor += step
    return slots


def published_host() -> CustomUser | None:
    """The account that should receive Bethany's published consultation hours.

    Prefer the clearcodereading.com mailbox, including when that address is
    connected to a different login. Otherwise use the one active CRM account
    named Bethany Fleming, so her hours still appear when her login email differs.
    """
    host = (
        CustomUser.objects.filter(
            email__iexact=PUBLISHED_EMAIL, is_active=True, is_deleted=False
        )
        .order_by("pk")
        .first()
    )
    if host is not None:
        return host
    from apps.crm_email.models import Mailbox

    mailbox = (
        Mailbox.objects.filter(email__iexact=PUBLISHED_EMAIL)
        .select_related("user")
        .order_by("pk")
        .first()
    )
    if mailbox is not None and mailbox.user.is_active and not mailbox.user.is_deleted:
        return mailbox.user
    from apps.crm.access import crm_owner_queryset

    # The public page already requires one Bethany Fleming. Use that same person
    # when her login email is not the published mailbox.
    matches = list(
        crm_owner_queryset().filter(
            first_name__iexact="Bethany", last_name__iexact="Fleming"
        )[:2]
    )
    return matches[0] if len(matches) == 1 else None


def ensure_published_hours() -> CustomUser | None:
    """Apply Bethany's published hours once, then keep the resulting signup times.

    Deleting or withdrawing a time later is kept. The schedule is not copied again.
    """
    existing = (
        ConsultationHoursSeed.objects.filter(key=PUBLISHED_KEY)
        .select_related("host")
        .first()
    )
    if existing is not None:
        if existing.host.is_active and not existing.host.is_deleted:
            materialize_open_slots(existing.host)
        return existing.host
    host = published_host()
    if host is None:
        return None
    try:
        with transaction.atomic():
            ConsultationHoursSeed.objects.create(key=PUBLISHED_KEY, host=host)
            ConsultationOpenWindow.objects.bulk_create(
                [
                    ConsultationOpenWindow(
                        host=host,
                        date=day,
                        starts_at=PUBLISHED_START,
                        ends_at=PUBLISHED_END,
                        timezone=PUBLISHED_TIMEZONE,
                    )
                    for day in PUBLISHED_DATES
                ],
                ignore_conflicts=True,
            )
    except IntegrityError:
        seeded = (
            ConsultationHoursSeed.objects.filter(key=PUBLISHED_KEY)
            .select_related("host")
            .first()
        )
        if seeded is None:
            raise
        host = seeded.host
    materialize_open_slots(host)
    return host


def materialize_open_slots(host: CustomUser, *, active: bool = True) -> int:
    """Create any missing 15-minute slots inside this host's open hours.

    An existing slot, including one that was withdrawn, blocks that start time
    so a later page view does not put a withdrawn time back on the calendar.
    """
    windows = list(ConsultationOpenWindow.objects.filter(host=host))
    if not windows:
        return 0
    now = timezone.now()
    horizon = now + timedelta(days=MAX_DAYS)
    planned: list[tuple[datetime, datetime]] = []
    for window in windows:
        for start, end in iter_window_slots(
            window.date, window.starts_at, window.ends_at, window.timezone
        ):
            if start > now and end <= horizon:
                planned.append((start, end))
    if not planned:
        return 0
    created = 0
    with transaction.atomic():
        CustomUser.objects.select_for_update().get(pk=host.pk)
        existing = list(
            ConsultationSlot.objects.filter(host=host).values_list(
                "starts_at", "ends_at"
            )
        )
        for start, end in planned:
            if any(begins < end and finishes > start for begins, finishes in existing):
                continue
            ConsultationSlot.objects.create(
                host=host, starts_at=start, ends_at=end, active=active
            )
            existing.append((start, end))
            created += 1
    return created
