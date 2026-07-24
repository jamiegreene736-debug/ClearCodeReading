from collections import Counter
from datetime import datetime, timedelta

from django.utils import timezone

from apps.curriculum.models import StudentPlacement
from apps.scheduling.models import ProviderAvailability, ScheduleBooking, WaitlistEntry
from apps.users.models import ChildProfile


def _window_key(window):
    return (window.get("day_of_week"), window.get("start_time"), window.get("end_time"), window.get("timezone", "UTC"))


def ranked_group_suggestions(center, max_position_gap=1):
    placements = list(
        StudentPlacement.objects.filter(center=center, is_active=True, is_deleted=False)
        .select_related("child", "curriculum", "current_position")
        .order_by("curriculum_id", "current_position__sequence_order")
    )
    providers = list(ProviderAvailability.objects.filter(center=center, is_active=True).select_related("specialist"))
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
                    }
                )
    unique = {}
    for suggestion in suggestions:
        key = (suggestion["specialist"], suggestion["methodology"], tuple(student["child"] for student in suggestion["students"]), tuple(suggestion["availability"].items()))
        unique[key] = suggestion
    return sorted(unique.values(), key=lambda item: (-item["score"], item["specialist_name"]))


def operations_metrics(center, start=None, end=None):
    start = start or timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = end or start + timedelta(days=28)
    providers = ProviderAvailability.objects.filter(center=center, is_active=True)
    capacity_hours = 0.0
    for provider in providers:
        for window in provider.windows:
            try:
                start_time = datetime.strptime(window["start_time"], "%H:%M")
                end_time = datetime.strptime(window["end_time"], "%H:%M")
                capacity_hours += (end_time - start_time).seconds / 3600 * 4
            except (KeyError, TypeError, ValueError):
                continue
    booked_seconds = sum(
        (booking.ends_at - booking.starts_at).total_seconds()
        for booking in ScheduleBooking.objects.filter(center=center, starts_at__gte=start, starts_at__lt=end, status__in=[ScheduleBooking.Status.APPROVED, ScheduleBooking.Status.CONFIRMED])
    )
    waitlist = list(WaitlistEntry.objects.filter(center=center, is_active=True))
    concentrations = Counter(entry.submarket or "Unspecified" for entry in waitlist)
    top_name, top_count = concentrations.most_common(1)[0] if concentrations else (None, 0)
    utilization = round((booked_seconds / 3600 / capacity_hours) * 100, 1) if capacity_hours else 0
    concentration = round((top_count / len(waitlist)) * 100, 1) if waitlist else 0
    return {
        "period": {"start": start, "end": end},
        "capacity_hours": round(capacity_hours, 1),
        "booked_hours": round(booked_seconds / 3600, 1),
        "utilization_percent": utilization,
        "utilization_threshold_reached": utilization >= 75,
        "waitlist_count": len(waitlist),
        "waitlist_threshold_reached": len(waitlist) >= 25,
        "top_submarket": top_name,
        "top_submarket_percent": concentration,
        "demand_concentration_threshold_reached": concentration >= 40,
    }
