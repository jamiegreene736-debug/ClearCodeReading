from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.curriculum.models import StudentPlacement
from apps.scheduling.models import ProviderAvailability, ScheduleBooking, ScheduleGroupProposal
from apps.schools.models import SchoolMembership


WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
DEFAULT_SESSION_MINUTES = 60
SLOT_INCREMENT_MINUTES = 30
MAX_OPTIMIZATION_DAYS = 93


class ProposalConflict(ValueError):
    pass


@dataclass(frozen=True)
class CandidateSlot:
    starts_at: datetime
    ends_at: datetime


def _parse_weekday(value) -> int | None:
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    if isinstance(value, str):
        return WEEKDAYS.get(value.strip().lower())
    return None


def _parse_time(value) -> time | None:
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def _window_bounds(window: dict, target_date: date) -> tuple[datetime, datetime] | None:
    if _parse_weekday(window.get("day_of_week")) != target_date.weekday():
        return None
    starts_at = _parse_time(window.get("start_time"))
    ends_at = _parse_time(window.get("end_time"))
    if starts_at is None or ends_at is None or ends_at <= starts_at:
        return None
    try:
        tz = ZoneInfo(window.get("timezone") or "UTC")
    except ZoneInfoNotFoundError:
        return None
    return datetime.combine(target_date, starts_at, tz), datetime.combine(target_date, ends_at, tz)


def _candidate_slots(windows: list[dict], start_date: date, end_date: date, duration_minutes: int) -> list[CandidateSlot]:
    slots: set[CandidateSlot] = set()
    target_date = start_date
    duration = timedelta(minutes=duration_minutes)
    increment = timedelta(minutes=SLOT_INCREMENT_MINUTES)
    while target_date <= end_date:
        for window in windows:
            bounds = _window_bounds(window, target_date)
            if bounds is None:
                continue
            starts_at, window_ends_at = bounds
            while starts_at + duration <= window_ends_at:
                slots.add(CandidateSlot(starts_at=starts_at, ends_at=starts_at + duration))
                starts_at += increment
        target_date += timedelta(days=1)
    return sorted(slots, key=lambda slot: slot.starts_at)


def _child_is_available(child, slot: CandidateSlot) -> bool:
    for window in child.availability_windows:
        bounds = _window_bounds(window, slot.starts_at.date())
        if bounds is None:
            continue
        starts_at, ends_at = bounds
        if starts_at.astimezone(UTC) <= slot.starts_at.astimezone(UTC) and (
            ends_at.astimezone(UTC) >= slot.ends_at.astimezone(UTC)
        ):
            return True
    return False


def _overlaps(starts_at: datetime, ends_at: datetime, slot: CandidateSlot) -> bool:
    return starts_at < slot.ends_at and ends_at > slot.starts_at


def _proposal_signature(center_id: int, specialist_id: int, curriculum_id: int, slot: CandidateSlot, child_ids: list[int]) -> str:
    raw = f"{center_id}:{specialist_id}:{curriculum_id}:{slot.starts_at.isoformat()}:{slot.ends_at.isoformat()}:{','.join(map(str, sorted(child_ids)))}"
    return sha256(raw.encode("utf-8")).hexdigest()


def _can_review(user, center_id: int) -> bool:
    if user.is_superuser or getattr(user, "role", None) == "super_admin":
        return True
    return user.school_memberships.filter(
        school_id=center_id,
        role__in=[SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN],
        is_deleted=False,
    ).exists()


def generate_group_proposals(
    *,
    center,
    start_date: date,
    end_date: date,
    specialist=None,
    created_by=None,
    max_position_gap: int = 1,
    session_minutes: int = DEFAULT_SESSION_MINUTES,
    limit: int = 50,
) -> dict:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date.")
    if (end_date - start_date).days + 1 > MAX_OPTIMIZATION_DAYS:
        raise ValueError(f"Optimization ranges cannot exceed {MAX_OPTIMIZATION_DAYS} days.")
    if session_minutes < 30 or session_minutes > 180:
        raise ValueError("session_minutes must be between 30 and 180.")
    if specialist is not None and not ProviderAvailability.objects.filter(
        center=center,
        specialist=specialist,
        is_active=True,
    ).exists():
        raise ValueError("The specialist has no active availability at this center.")

    placements = list(
        StudentPlacement.objects.filter(
            center=center,
            is_active=True,
            is_deleted=False,
            child__school=center,
            child__is_deleted=False,
            curriculum__is_active=True,
            curriculum__is_deleted=False,
            current_position__is_deleted=False,
        )
        .select_related("child", "curriculum", "current_position")
        .order_by("curriculum_id", "current_position__sequence_order", "child_id")
    )
    excluded = [
        {
            "child": placement.child_id,
            "display_name": str(placement.child),
            "reason": "IEP authorization pending",
        }
        for placement in placements
        if not placement.child.idea_services_authorized
    ]
    eligible = [placement for placement in placements if placement.child.idea_services_authorized]

    providers = ProviderAvailability.objects.filter(center=center, is_active=True).select_related("specialist")
    if specialist is not None:
        providers = providers.filter(specialist=specialist)

    ranked: list[dict] = []
    for availability in providers:
        existing_bookings = list(
            ScheduleBooking.objects.filter(
                center=center,
                status__in=[ScheduleBooking.Status.APPROVED, ScheduleBooking.Status.CONFIRMED],
                starts_at__date__lte=end_date,
                ends_at__date__gte=start_date,
            )
            .filter(
                Q(specialist=availability.specialist)
                | Q(child_id__in=[placement.child_id for placement in eligible])
            )
            .only("child_id", "specialist_id", "starts_at", "ends_at")
        )
        provider_conflicts = [
            booking for booking in existing_bookings if booking.specialist_id == availability.specialist_id
        ]
        child_conflicts: dict[int, list[ScheduleBooking]] = {}
        for booking in existing_bookings:
            child_conflicts.setdefault(booking.child_id, []).append(booking)
        for slot in _candidate_slots(availability.windows, start_date, end_date, session_minutes):
            if any(_overlaps(booking.starts_at, booking.ends_at, slot) for booking in provider_conflicts):
                continue
            by_curriculum: dict[int, list[StudentPlacement]] = {}
            for placement in eligible:
                if not _child_is_available(placement.child, slot):
                    continue
                if any(
                    _overlaps(booking.starts_at, booking.ends_at, slot)
                    for booking in child_conflicts.get(placement.child_id, [])
                ):
                    continue
                by_curriculum.setdefault(placement.curriculum_id, []).append(placement)

            for curriculum_id, candidates in by_curriculum.items():
                candidates.sort(key=lambda item: (item.current_position.sequence_order, item.child_id))
                best: list[StudentPlacement] = []
                for index, anchor in enumerate(candidates):
                    group = [
                        candidate
                        for candidate in candidates[index:]
                        if candidate.current_position.sequence_order - anchor.current_position.sequence_order <= max_position_gap
                    ][: availability.max_group_size]
                    if len(group) > len(best):
                        best = group
                if len(best) < 2:
                    continue
                spread = best[-1].current_position.sequence_order - best[0].current_position.sequence_order
                score = max(0, min(100, 70 + len(best) * 10 - spread * 10))
                ranked.append(
                    {
                        "availability": availability,
                        "slot": slot,
                        "curriculum_id": curriculum_id,
                        "placements": best,
                        "score": score,
                    }
                )

    ranked.sort(
        key=lambda item: (
            -item["score"],
            item["slot"].starts_at,
            item["availability"].specialist_id,
            item["curriculum_id"],
        )
    )
    created: list[ScheduleGroupProposal] = []
    with transaction.atomic():
        for item in ranked[:limit]:
            child_ids = [placement.child_id for placement in item["placements"]]
            signature = _proposal_signature(
                center.id,
                item["availability"].specialist_id,
                item["curriculum_id"],
                item["slot"],
                child_ids,
            )
            proposal, was_created = ScheduleGroupProposal.objects.get_or_create(
                center=center,
                signature=signature,
                defaults={
                    "specialist": item["availability"].specialist,
                    "curriculum_id": item["curriculum_id"],
                    "starts_at": item["slot"].starts_at,
                    "ends_at": item["slot"].ends_at,
                    "score": item["score"],
                    "rationale": "Same methodology, adjacent sequence positions, and overlapping child/provider availability.",
                    "created_by": created_by,
                    "metadata": {
                        "advisory": True,
                        "approval_required": True,
                        "max_position_gap": max_position_gap,
                        "sequence_orders": [
                            placement.current_position.sequence_order for placement in item["placements"]
                        ],
                    },
                },
            )
            if not was_created:
                continue
            proposal.children.set(child_ids)
            ScheduleBooking.objects.bulk_create(
                [
                    ScheduleBooking(
                        center=center,
                        proposal=proposal,
                        child=placement.child,
                        specialist=item["availability"].specialist,
                        starts_at=item["slot"].starts_at,
                        ends_at=item["slot"].ends_at,
                        status=ScheduleBooking.Status.PROPOSED,
                        metadata={"position_code": placement.current_position.code},
                    )
                    for placement in item["placements"]
                ]
            )
            created.append(proposal)

    return {
        "proposals": created,
        "excluded_pending_consent": excluded,
        "advisory": True,
        "approval_required": True,
    }


@transaction.atomic
def approve_group_proposal(proposal: ScheduleGroupProposal, reviewed_by) -> ScheduleGroupProposal:
    proposal = (
        ScheduleGroupProposal.objects.select_for_update()
        .select_related("center", "curriculum", "specialist")
        .get(pk=proposal.pk)
    )
    if proposal.status != ScheduleGroupProposal.Status.PROPOSED:
        raise ProposalConflict("Only proposed groups can be approved.")
    if not _can_review(reviewed_by, proposal.center_id):
        raise ProposalConflict("Center operations leadership must approve this proposal.")

    children = list(proposal.children.select_for_update())
    unauthorized = [str(child) for child in children if not child.idea_services_authorized]
    if unauthorized:
        raise ProposalConflict(f"IEP authorization is pending for: {', '.join(unauthorized)}.")
    placements = list(
        StudentPlacement.objects.filter(
            center=proposal.center,
            child__in=children,
            is_active=True,
            is_deleted=False,
        ).select_related("current_position", "curriculum")
    )
    if len(placements) != len(children) or any(
        placement.curriculum_id != proposal.curriculum_id for placement in placements
    ):
        raise ProposalConflict("The active placement or methodology changed; regenerate this proposal.")
    sequence_orders = [placement.current_position.sequence_order for placement in placements]
    max_gap = int(proposal.metadata.get("max_position_gap", 1))
    if max(sequence_orders) - min(sequence_orders) > max_gap:
        raise ProposalConflict("The group is no longer sequence-compatible; regenerate this proposal.")

    reviewed_at = timezone.now()
    updated = proposal.bookings.filter(status=ScheduleBooking.Status.PROPOSED).update(
        status=ScheduleBooking.Status.APPROVED,
        approved_by=reviewed_by,
        approved_at=reviewed_at,
        sync_status=ScheduleBooking.SyncStatus.PENDING,
    )
    if updated != len(children):
        raise ProposalConflict("The proposal booking set changed; regenerate this proposal.")
    proposal.status = ScheduleGroupProposal.Status.APPROVED
    proposal.reviewed_by = reviewed_by
    proposal.reviewed_at = reviewed_at
    proposal.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    return proposal


@transaction.atomic
def reject_group_proposal(proposal: ScheduleGroupProposal, reviewed_by) -> ScheduleGroupProposal:
    proposal = ScheduleGroupProposal.objects.select_for_update().get(pk=proposal.pk)
    if proposal.status != ScheduleGroupProposal.Status.PROPOSED:
        raise ProposalConflict("Only proposed groups can be rejected.")
    if not _can_review(reviewed_by, proposal.center_id):
        raise ProposalConflict("Center operations leadership must reject this proposal.")
    reviewed_at = timezone.now()
    proposal.bookings.filter(status=ScheduleBooking.Status.PROPOSED).update(status=ScheduleBooking.Status.CANCELED)
    proposal.status = ScheduleGroupProposal.Status.REJECTED
    proposal.reviewed_by = reviewed_by
    proposal.reviewed_at = reviewed_at
    proposal.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    return proposal
