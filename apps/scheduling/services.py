from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone

from apps.curriculum.models import StudentPlacement
from apps.scheduling.integrations import SchedulerAdapter, SchedulerError
from apps.scheduling.models import Group, ProviderAvailability, ScheduleBooking, WaitlistEntry
from apps.sessions.models import Session


EXPANSION_THRESHOLDS = {
    "utilization_percent": 75,
    "waitlist_count": 25,
    "submarket_demand_percent": 40,
    "sustained_days": 28,
}


def _window_key(window):
    return (
        window.get("day_of_week"),
        window.get("start_time"),
        window.get("end_time"),
        window.get("timezone", "UTC"),
    )


def ranked_group_suggestions(center, max_position_gap=1):
    """Read-only compatibility feed retained for the existing staff dashboard."""
    placements = list(
        StudentPlacement.objects.filter(center=center, is_active=True, is_deleted=False)
        .select_related("child", "curriculum", "current_position")
        .order_by("curriculum_id", "current_position__sequence_order")
    )
    providers = list(ProviderAvailability.objects.filter(center=center, is_active=True).select_related("specialist"))
    existing_groups = {
        (group.curriculum_id, group.sequence_start_id, group.sequence_end_id, group.primary_specialist_id): group.id
        for group in Group.objects.filter(center=center, is_active=True)
    }
    suggestions = []
    for provider in providers:
        provider_windows = {_window_key(window) for window in provider.windows}
        for anchor in placements:
            compatible = []
            pending = []
            for placement in placements:
                if placement.curriculum_id != anchor.curriculum_id:
                    continue
                if abs(placement.current_position.sequence_order - anchor.current_position.sequence_order) > max_position_gap:
                    continue
                if not placement.child.idea_services_authorized:
                    pending.append({"child": placement.child_id, "display_name": str(placement.child), "reason": "IEP authorization pending"})
                    continue
                overlap = provider_windows & {_window_key(window) for window in placement.child.availability_windows}
                if overlap:
                    compatible.append((placement, sorted(overlap)[0]))
            for window in sorted({item[1] for item in compatible}):
                members = [item[0] for item in compatible if item[1] == window][: provider.max_group_size]
                if len(members) < 2:
                    continue
                spread = max(p.current_position.sequence_order for p in members) - min(p.current_position.sequence_order for p in members)
                sequence_start = min(members, key=lambda placement: placement.current_position.sequence_order).current_position
                sequence_end = max(members, key=lambda placement: placement.current_position.sequence_order).current_position
                existing_group_id = existing_groups.get(
                    (anchor.curriculum_id, sequence_start.id, sequence_end.id, provider.specialist_id)
                )
                suggestions.append(
                    {
                        "status": "proposed",
                        "approval_required": True,
                        "specialist": provider.specialist_id,
                        "specialist_name": provider.specialist.get_full_name() or provider.specialist.email,
                        "methodology": anchor.curriculum.code,
                        "availability": {"day_of_week": window[0], "start_time": window[1], "end_time": window[2], "timezone": window[3]},
                        "students": [{"child": p.child_id, "display_name": str(p.child), "position_code": p.current_position.code} for p in members],
                        "score": 100 - (spread * 15),
                        "rationale": "Same methodology, adjacent sequence positions, and shared student/provider availability.",
                        "pending_authorizations": pending,
                        "existing_group": existing_group_id,
                        "suggested_group": {
                            "center": center.id,
                            "name": (
                                f"{anchor.curriculum.get_code_display()} "
                                f"{sequence_start.code}-{sequence_end.code}"
                            ),
                            "curriculum": anchor.curriculum_id,
                            "sequence_start": sequence_start.id,
                            "sequence_end": sequence_end.id,
                            "students": [placement.child_id for placement in members],
                            "primary_specialist": provider.specialist_id,
                            "is_active": True,
                            "notes": "Suggested from active placements at compatible sequence positions.",
                        },
                    }
                )
    unique = {}
    for suggestion in suggestions:
        key = (suggestion["specialist"], suggestion["methodology"], tuple(student["child"] for student in suggestion["students"]), tuple(suggestion["availability"].items()))
        unique[key] = suggestion
    return sorted(unique.values(), key=lambda item: (-item["score"], item["specialist_name"]))


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
