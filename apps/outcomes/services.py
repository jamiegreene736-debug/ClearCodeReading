from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean, median

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from apps.curriculum.models import StudentPlacement
from apps.outcomes.models import DeIdentifiedOutcomeSnapshot, build_center_key
from apps.progress.models import MasteryRecord
from apps.schools.models import School, SchoolMembership
from apps.sessions.models import Session, SkillObservation
from apps.users.models import ChildProfile, CustomUser


DEFAULT_METRIC_SCOPE = "core_outcomes"
DEFAULT_AGGREGATE_VERSION = "v1"


@dataclass(frozen=True)
class OutcomeWindow:
    window_type: str
    start: date
    end: date


def previous_quarter(today: date | None = None) -> OutcomeWindow:
    today = today or timezone.localdate()
    current_quarter = ((today.month - 1) // 3) + 1
    first_current_month = ((current_quarter - 1) * 3) + 1
    current_start = date(today.year, first_current_month, 1)
    previous_end = current_start - timedelta(days=1)
    previous_quarter_number = ((previous_end.month - 1) // 3) + 1
    previous_start_month = ((previous_quarter_number - 1) * 3) + 1
    return OutcomeWindow(
        window_type=DeIdentifiedOutcomeSnapshot.WindowType.QUARTER,
        start=date(previous_end.year, previous_start_month, 1),
        end=previous_end,
    )


def parse_window(window_type: str, start: date | None = None, end: date | None = None) -> OutcomeWindow:
    if window_type == DeIdentifiedOutcomeSnapshot.WindowType.CUSTOM:
        if start is None or end is None:
            raise ValueError("Custom outcome windows require both start and end dates.")
        if end < start:
            raise ValueError("Outcome window end must be on or after start.")
        return OutcomeWindow(window_type=window_type, start=start, end=end)

    if start is None:
        if window_type == DeIdentifiedOutcomeSnapshot.WindowType.QUARTER:
            return previous_quarter()
        raise ValueError("Non-quarter windows require an explicit start date.")

    if window_type == DeIdentifiedOutcomeSnapshot.WindowType.MONTH:
        next_month = date(start.year + int(start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
        return OutcomeWindow(
            window_type=window_type,
            start=date(start.year, start.month, 1),
            end=next_month - timedelta(days=1),
        )
    if window_type == DeIdentifiedOutcomeSnapshot.WindowType.QUARTER:
        quarter = ((start.month - 1) // 3) + 1
        quarter_start_month = ((quarter - 1) * 3) + 1
        quarter_start = date(start.year, quarter_start_month, 1)
        quarter_end_month = quarter_start_month + 2
        next_month = date(
            start.year + int(quarter_end_month == 12),
            1 if quarter_end_month == 12 else quarter_end_month + 1,
            1,
        )
        return OutcomeWindow(window_type=window_type, start=quarter_start, end=next_month - timedelta(days=1))
    if window_type == DeIdentifiedOutcomeSnapshot.WindowType.YEAR:
        return OutcomeWindow(window_type=window_type, start=date(start.year, 1, 1), end=date(start.year, 12, 31))

    raise ValueError(f"Unsupported outcome window type: {window_type}")


def aggregate_outcomes(
    *,
    window: OutcomeWindow,
    aggregate_version: str = DEFAULT_AGGREGATE_VERSION,
    metric_scope: str = DEFAULT_METRIC_SCOPE,
    center: School | int | str | None = None,
    min_cohort_size: int | None = None,
) -> list[DeIdentifiedOutcomeSnapshot]:
    center_id = resolve_center_id(center)
    privacy_floor = settings.OUTCOMES_MIN_COHORT_SIZE if min_cohort_size is None else min_cohort_size
    if privacy_floor < 2:
        raise ValueError("The outcomes privacy floor must be at least 2.")

    completed_sessions = (
        Session.objects.filter(
            is_deleted=False,
            status=Session.Status.COMPLETED,
            ended_at__date__gte=window.start,
            ended_at__date__lte=window.end,
            child__is_deleted=False,
        )
        .select_related("center", "child", "curriculum_position__curriculum")
        .order_by("center_id", "child_id", "ended_at")
    )
    if center_id is not None:
        completed_sessions = completed_sessions.filter(center_id=center_id)

    groups: dict[tuple[int, str, str], dict] = defaultdict(_empty_group)
    for session in completed_sessions:
        methodology = session.curriculum_position.curriculum.code
        grade_band = grade_band_for(session.child.grade_level)
        group_key = (session.center_id, methodology, grade_band)
        group = groups[group_key]
        group["children"].add(session.child_id)
        group["session_ids"].add(session.id)
        group["accuracy_values"].append(float(session.accuracy_rate)) if session.accuracy_rate is not None else None
        group["accuracy_numerator"] += session.accuracy_numerator or 0
        group["accuracy_denominator"] += session.accuracy_denominator or 0
        group["structured_observations"] += len(session.error_patterns) + len(session.behavioral_observations)
        group["position_ids"].add(session.curriculum_position_id)
        cumulative_sessions = session.time_to_mastery_signals.get("cumulative_sessions_at_position")
        if isinstance(cumulative_sessions, int) and cumulative_sessions > 0:
            group["sessions_to_position"].append(cumulative_sessions)

    skill_observations = SkillObservation.objects.filter(
        is_deleted=False,
        session__is_deleted=False,
        session__status=Session.Status.COMPLETED,
        session__ended_at__date__gte=window.start,
        session__ended_at__date__lte=window.end,
        child__is_deleted=False,
    ).select_related("child", "curriculum_position__curriculum")
    if center_id is not None:
        skill_observations = skill_observations.filter(center_id=center_id)
    for observation in skill_observations:
        methodology = observation.curriculum_position.curriculum.code
        grade_band = grade_band_for(observation.child.grade_level)
        group = groups[(observation.center_id, methodology, grade_band)]
        group["children"].add(observation.child_id)
        group["skill_observation_ids"].add(observation.id)
        group["position_ids"].add(observation.curriculum_position_id)

    placements = (
        StudentPlacement.objects.filter(
            is_deleted=False,
            placed_at__date__lte=window.end,
            child__is_deleted=False,
            child__school__isnull=False,
        )
        .select_related("center", "child", "curriculum", "current_position")
    )
    if center_id is not None:
        placements = placements.filter(center_id=center_id)
    for placement in placements:
        methodology = placement.curriculum.code
        grade_band = grade_band_for(placement.child.grade_level)
        group_key = (placement.center_id, methodology, grade_band)
        if group_key in groups or placement.placed_at.date() >= window.start:
            groups[group_key]["placed_children"].add(placement.child_id)
            groups[group_key]["position_ids"].add(placement.current_position_id)

    mastery_records = (
        MasteryRecord.objects.filter(
            is_deleted=False,
            mastered_at__date__gte=window.start,
            mastered_at__date__lte=window.end,
            child__is_deleted=False,
            child__school__isnull=False,
        )
        .select_related("child__school", "progress")
    )
    if center_id is not None:
        mastery_records = mastery_records.filter(child__school_id=center_id)
    for mastery in mastery_records:
        placement = _placement_for_child(mastery.child_id, mastery.mastered_at)
        if placement is None:
            continue
        methodology = placement.curriculum.code
        grade_band = grade_band_for(mastery.child.grade_level)
        group_key = (placement.center_id, methodology, grade_band)
        group = groups[group_key]
        group["children"].add(mastery.child_id)
        group["placed_children"].add(mastery.child_id)
        group["mastery_ids"].add(mastery.id)
        group["mastered_children"].add(mastery.child_id)
        sessions_to_mastery = _sessions_to_mastery(mastery.child_id, placement.curriculum_id, mastery.mastered_at)
        if sessions_to_mastery is not None:
            group["sessions_to_mastery"].append(sessions_to_mastery)

    written = []
    for group_key, group in sorted(groups.items()):
        center_id, methodology, grade_band = group_key
        metrics = _build_metrics(group)
        if metrics["cohort_students"] < privacy_floor:
            continue
        source_counts = {
            "cohort_students": metrics["cohort_students"],
            "completed_sessions": len(group["session_ids"]),
            "mastery_records": len(group["mastery_ids"]),
            "curriculum_positions": len(group["position_ids"]),
            "skill_observations": len(group["skill_observation_ids"]),
            "structured_session_signals": group["structured_observations"],
        }
        snapshot = _create_snapshot_once(
            center_id=center_id,
            methodology=methodology,
            grade_band=grade_band,
            window=window,
            metrics=metrics,
            source_counts=source_counts,
            metric_scope=metric_scope,
            aggregate_version=aggregate_version,
            privacy_floor=privacy_floor,
        )
        written.append(snapshot)
    return written


def _empty_group():
    return {
        "children": set(),
        "placed_children": set(),
        "session_ids": set(),
        "mastery_ids": set(),
        "mastered_children": set(),
        "accuracy_values": [],
        "accuracy_numerator": 0,
        "accuracy_denominator": 0,
        "sessions_to_mastery": [],
        "sessions_to_position": [],
        "position_ids": set(),
        "skill_observation_ids": set(),
        "structured_observations": 0,
    }


def _build_metrics(group: dict) -> dict:
    cohort_count = len(group["children"] | group["placed_children"])
    mastered_count = len(group["mastered_children"])
    accuracy_values = group["accuracy_values"]
    sessions_to_mastery = group["sessions_to_mastery"]
    sessions_to_position = group["sessions_to_position"]
    return {
        "cohort_students": cohort_count,
        "completed_sessions": len(group["session_ids"]),
        "mastery_events": len(group["mastery_ids"]),
        "students_with_mastery": mastered_count,
        "skill_mastery_rate": _safe_rate(mastered_count, cohort_count),
        "average_accuracy_rate": _round(mean(accuracy_values)) if accuracy_values else None,
        "weighted_accuracy_rate": _safe_rate(group["accuracy_numerator"], group["accuracy_denominator"]),
        "mean_sessions_to_mastery": _round(mean(sessions_to_mastery)) if sessions_to_mastery else None,
        "median_sessions_to_mastery": _round(median(sessions_to_mastery)) if sessions_to_mastery else None,
        "mean_sessions_to_position": _round(mean(sessions_to_position)) if sessions_to_position else None,
        "median_sessions_to_position": _round(median(sessions_to_position)) if sessions_to_position else None,
        "retention": {
            "active_student_count": cohort_count,
            "students_with_completed_sessions": len(group["children"]),
        },
    }


def _create_snapshot_once(
    *,
    center_id: int,
    methodology: str,
    grade_band: str,
    window: OutcomeWindow,
    metrics: dict,
    source_counts: dict,
    metric_scope: str,
    aggregate_version: str,
    privacy_floor: int,
) -> DeIdentifiedOutcomeSnapshot:
    existing = DeIdentifiedOutcomeSnapshot.objects.filter(
        center_id=center_id,
        methodology=methodology,
        grade_band=grade_band,
        window_type=window.window_type,
        window_start=window.start,
        window_end=window.end,
        metric_scope=metric_scope,
        aggregate_version=aggregate_version,
    ).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return DeIdentifiedOutcomeSnapshot.objects.create(
                center_id=center_id,
                center_key=build_center_key(center_id),
                methodology=methodology,
                grade_band=grade_band,
                window_type=window.window_type,
                window_start=window.start,
                window_end=window.end,
                metric_scope=metric_scope,
                aggregate_version=aggregate_version,
                privacy_floor=privacy_floor,
                metrics=metrics,
                source_counts=source_counts,
            )
    except IntegrityError:
        return DeIdentifiedOutcomeSnapshot.objects.get(
            center_id=center_id,
            methodology=methodology,
            grade_band=grade_band,
            window_type=window.window_type,
            window_start=window.start,
            window_end=window.end,
            metric_scope=metric_scope,
            aggregate_version=aggregate_version,
        )


def grade_band_for(grade_level: str) -> str:
    if grade_level in {ChildProfile.GradeLevel.PRE_K, ChildProfile.GradeLevel.KINDERGARTEN}:
        return "pre_k_k"
    if grade_level in {ChildProfile.GradeLevel.GRADE_1, ChildProfile.GradeLevel.GRADE_2}:
        return "grade_1_2"
    if grade_level in {ChildProfile.GradeLevel.GRADE_3, ChildProfile.GradeLevel.GRADE_4, ChildProfile.GradeLevel.GRADE_5}:
        return "grade_3_5"
    return "unspecified"


def _placement_for_child(child_id: int, at_time):
    return (
        StudentPlacement.objects.filter(
            child_id=child_id,
            placed_at__lte=at_time,
            is_deleted=False,
        )
        .select_related("curriculum")
        .order_by("-placed_at")
        .first()
    )


def _sessions_to_mastery(child_id: int, curriculum_id: int, mastered_at):
    count = Session.objects.filter(
        child_id=child_id,
        curriculum_position__curriculum_id=curriculum_id,
        status=Session.Status.COMPLETED,
        is_deleted=False,
        ended_at__isnull=False,
        ended_at__lte=mastered_at,
    ).aggregate(count=Count("id"))["count"]
    return count or None


def _safe_rate(numerator: int | Decimal | float, denominator: int | Decimal | float):
    if not denominator:
        return None
    return _round((float(numerator) / float(denominator)) * 100)


def _round(value):
    return round(float(value), 2)


def run_outcomes_aggregation(
    period_start: date,
    period_end: date,
    center: School | int | str | None = None,
    *,
    aggregate_version: str = DEFAULT_AGGREGATE_VERSION,
    metric_scope: str = DEFAULT_METRIC_SCOPE,
    min_cohort_size: int | None = None,
) -> list[DeIdentifiedOutcomeSnapshot]:
    """Aggregate an explicit inclusive period without exposing child-level output."""

    window = parse_window(
        DeIdentifiedOutcomeSnapshot.WindowType.CUSTOM,
        start=period_start,
        end=period_end,
    )
    return aggregate_outcomes(
        window=window,
        center=center,
        aggregate_version=aggregate_version,
        metric_scope=metric_scope,
        min_cohort_size=min_cohort_size,
    )


def resolve_center_id(center: School | int | str | None) -> int | None:
    if center is None:
        return None
    if isinstance(center, School):
        return center.pk
    if isinstance(center, int) or str(center).isdigit():
        center_id = int(center)
        if not School.objects.filter(pk=center_id, is_deleted=False).exists():
            raise ValueError(f"Unknown center: {center}")
        return center_id
    center_id = (
        School.objects.filter(slug=str(center), is_deleted=False)
        .values_list("id", flat=True)
        .first()
    )
    if center_id is None:
        raise ValueError(f"Unknown center: {center}")
    return center_id


def latest_snapshots_for_user(user):
    queryset = DeIdentifiedOutcomeSnapshot.objects.all()
    if user.is_superuser or getattr(user, "role", None) == CustomUser.Role.SUPER_ADMIN:
        return queryset
    return queryset.filter(
        center__memberships__user=user,
        center__memberships__is_deleted=False,
    ).filter(
        models_membership_filter()
    ).distinct()


def models_membership_filter():
    from django.db.models import Q

    return Q(center__memberships__role__in=[SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN]) | Q(
        center__memberships__permissions__outcomes_reports=True
    )
