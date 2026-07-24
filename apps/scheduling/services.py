from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone

from apps.curriculum.models import StudentPlacement
from apps.scheduling.integrations import SchedulerAdapter, SchedulerError
from apps.scheduling.models import ProviderAvailability, ScheduleBooking, WaitlistEntry
from apps.sessions.models import Session


EXPANSION_THRESHOLDS = {
    "utilization_percent": 75,
    "waitlist_count": 25,
    "submarket_demand_percent": 40,
    "sustained_days": 28,
}


def ranked_group_suggestions(center, max_position_gap=1):
    """Read-only compatibility feed retained for the existing staff dashboard."""
    placements = list(
        StudentPlacement.objects.filter(center=center, is_active=True, is_deleted=False)
        .select_related("child", "curriculum", "current_position")
        .order_by("curriculum_id", "current_position__sequence_order")
    )
    pending = [
        {"child": placement.child_id, "display_name": str(placement.child), "reason": "IEP authorization pending"}
        for placement in placements
        if not placement.child.idea_services_authorized
    ]
    suggestions = []
    for curriculum_id in {placement.curriculum_id for placement in placements}:
        candidates = [
            placement
            for placement in placements
            if placement.curriculum_id == curriculum_id and placement.child.idea_services_authorized
        ]
        candidates.sort(key=lambda placement: placement.current_position.sequence_order)
        for index, anchor in enumerate(candidates):
            group = [
                candidate
                for candidate in candidates[index:]
                if candidate.current_position.sequence_order - anchor.current_position.sequence_order <= max_position_gap
            ]
            if len(group) < 2:
                continue
            spread = group[-1].current_position.sequence_order - group[0].current_position.sequence_order
            suggestions.append(
                {
                    "methodology": anchor.curriculum.code,
                    "students": [
                        {
                            "child": placement.child_id,
                            "display_name": str(placement.child),
                            "position_code": placement.current_position.code,
                        }
                        for placement in group
                    ],
                    "score": max(0, 80 - spread * 10 + len(group) * 5),
                    "approval_required": True,
                    "pending_authorizations": pending,
                }
            )
            break
    return sorted(suggestions, key=lambda item: -item["score"])


def _window_capacity_hours(windows: list[dict], start_date: date, end_date: date) -> float:
    total = 0.0
    current = start_date
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    while current <= end_date:
        for window in windows:
            weekday = window.get("day_of_week")
            if isinstance(weekday, str):
                weekday = weekdays.get(weekday.lower())
            if weekday != current.weekday():
                continue
            try:
                starts_at = time.fromisoformat(window["start_time"])
                ends_at = time.fromisoformat(window["end_time"])
                ZoneInfo(window.get("timezone") or "UTC")
            except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
                continue
            if ends_at > starts_at:
                start_dt = datetime.combine(current, starts_at)
                end_dt = datetime.combine(current, ends_at)
                total += (end_dt - start_dt).total_seconds() / 3600
        current += timedelta(days=1)
    return total


def operations_metrics(center, start=None, end=None):
    start = start or timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = end or start + timedelta(days=28)
    if end <= start:
        raise ValueError("Metrics end must be after start.")

    capacity_hours = sum(
        _window_capacity_hours(provider.windows, start.date(), (end - timedelta(microseconds=1)).date())
        for provider in ProviderAvailability.objects.filter(center=center, is_active=True)
    )
    delivery_slots: set[tuple[int, datetime, datetime]] = set()
    for booking in ScheduleBooking.objects.filter(
        center=center,
        starts_at__gte=start,
        starts_at__lt=end,
        status=ScheduleBooking.Status.CONFIRMED,
    ):
        delivery_slots.add((booking.specialist_id, booking.starts_at, booking.ends_at))
    for session in Session.objects.filter(
        center=center,
        is_deleted=False,
        status=Session.Status.COMPLETED,
        started_at__gte=start,
        started_at__lt=end,
        ended_at__isnull=False,
    ):
        delivery_slots.add((session.specialist_id, session.started_at, session.ended_at))

    delivered_hours = sum((slot_end - slot_start).total_seconds() for _, slot_start, slot_end in delivery_slots) / 3600
    waitlist = list(WaitlistEntry.objects.filter(center=center, is_active=True))
    concentrations = Counter(entry.submarket.strip() or "Unspecified" for entry in waitlist)
    top_name, top_count = concentrations.most_common(1)[0] if concentrations else (None, 0)
    utilization = round((delivered_hours / capacity_hours) * 100, 1) if capacity_hours else 0.0
    concentration = round((top_count / len(waitlist)) * 100, 1) if waitlist else 0.0
    period_days = (end - start).total_seconds() / 86400
    sustained_utilization = period_days >= EXPANSION_THRESHOLDS["sustained_days"] and utilization >= EXPANSION_THRESHOLDS[
        "utilization_percent"
    ]
    waitlist_signal = len(waitlist) >= EXPANSION_THRESHOLDS["waitlist_count"]
    concentration_signal = concentration >= EXPANSION_THRESHOLDS["submarket_demand_percent"]
    return {
        "period": {"start": start, "end": end, "days": round(period_days, 1)},
        "capacity_hours": round(capacity_hours, 1),
        "delivered_hours": round(delivered_hours, 1),
        "utilization_percent": utilization,
        "active_waitlist_count": len(waitlist),
        "submarket_concentration": [
            {
                "submarket": submarket,
                "count": count,
                "percent": round(count / len(waitlist) * 100, 1),
            }
            for submarket, count in concentrations.most_common()
        ],
        "expansion_thresholds": EXPANSION_THRESHOLDS,
        "signals": {
            "sustained_utilization": sustained_utilization,
            "waitlist": waitlist_signal,
            "submarket_concentration": concentration_signal,
            "expansion_review_recommended": sustained_utilization and waitlist_signal and concentration_signal,
        },
        # Backward-compatible summary fields used by the current staff portal.
        "booked_hours": round(delivered_hours, 1),
        "waitlist_count": len(waitlist),
        "top_submarket": top_name,
        "top_submarket_percent": concentration,
        "utilization_threshold_reached": sustained_utilization,
        "waitlist_threshold_reached": waitlist_signal,
        "demand_concentration_threshold_reached": concentration_signal,
    }


def sync_booking(booking: ScheduleBooking, adapter: SchedulerAdapter) -> ScheduleBooking:
    if booking.status not in [ScheduleBooking.Status.APPROVED, ScheduleBooking.Status.CONFIRMED]:
        raise ValueError("Staff approval is required before scheduler sync.")
    if not booking.child.idea_services_authorized:
        raise ValueError("IEP authorization is no longer valid; external sync was blocked.")

    booking.sync_attempts += 1
    booking.last_sync_at = timezone.now()
    try:
        external_id = adapter.upsert_booking(booking)
    except Exception as error:
        booking.sync_status = ScheduleBooking.SyncStatus.ERROR
        booking.sync_error = str(error) if isinstance(error, SchedulerError) else "Unexpected scheduler adapter failure."
        booking.save(update_fields=["sync_status", "sync_error", "sync_attempts", "last_sync_at", "updated_at"])
        return booking

    booking.scheduler_provider = adapter.provider
    booking.external_booking_id = str(external_id)
    booking.sync_status = ScheduleBooking.SyncStatus.SYNCED
    booking.sync_error = ""
    booking.status = ScheduleBooking.Status.CONFIRMED
    booking.save(
        update_fields=[
            "scheduler_provider",
            "external_booking_id",
            "sync_status",
            "sync_error",
            "sync_attempts",
            "last_sync_at",
            "status",
            "updated_at",
        ]
    )
    return booking


@transaction.atomic
def reconcile_remote_bookings(*, center, adapter: SchedulerAdapter, start_date=None, end_date=None, updated_since=None):
    remote_bookings = adapter.pull_bookings(
        start_date=start_date,
        end_date=end_date,
        updated_since=updated_since,
    )
    reconciled = []
    skipped = []
    for remote in remote_bookings:
        booking = (
            ScheduleBooking.objects.select_for_update()
            .filter(
                center=center,
                scheduler_provider=adapter.provider,
                external_booking_id=remote.external_booking_id,
            )
            .select_related("child")
            .first()
        )
        if booking is None:
            skipped.append({"external_booking_id": remote.external_booking_id, "reason": "unknown_local_booking"})
            continue
        booking.last_sync_at = timezone.now()
        booking.sync_attempts += 1
        booking.metadata = {**booking.metadata, "last_remote_snapshot": remote.raw}
        if remote.canceled:
            booking.status = ScheduleBooking.Status.CANCELED
            booking.sync_status = ScheduleBooking.SyncStatus.SYNCED
            booking.sync_error = ""
        elif not booking.child.idea_services_authorized:
            booking.sync_status = ScheduleBooking.SyncStatus.ERROR
            booking.sync_error = "IEP authorization is no longer valid; cancel or resolve this remote booking."
        else:
            booking.starts_at = remote.starts_at
            booking.ends_at = remote.ends_at
            booking.status = ScheduleBooking.Status.CONFIRMED
            booking.sync_status = ScheduleBooking.SyncStatus.SYNCED
            booking.sync_error = ""
        booking.save()
        reconciled.append(booking.id)
    return {"provider": adapter.provider, "reconciled": len(reconciled), "booking_ids": reconciled, "skipped": skipped}
