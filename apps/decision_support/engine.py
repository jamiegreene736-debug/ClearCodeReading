from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from math import ceil
from statistics import median

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.curriculum.models import Curriculum, StudentPlacement
from apps.decision_support.models import GrowthFlag, MilestonePrediction
from apps.schools.models import SchoolMembership
from apps.sessions.models import Session


ENGINE_VERSION = "deterministic-2026.1"


@dataclass(frozen=True)
class FlagResult:
    code: str
    severity: str
    evidence: dict
    explanation: str
    advisory_recommendation: str


def _accuracy(session):
    value = session.accuracy_rate
    return float(value) if isinstance(value, Decimal) else value


def _completed_at(session):
    return session.ended_at or session.scheduled_start


def _session_evidence(session):
    return {
        "session_id": session.id,
        "completed_at": _completed_at(session).isoformat(),
        "accuracy": _accuracy(session),
        "reteach": bool((session.time_to_mastery_signals or {}).get("reteach")),
        "error_patterns": [
            {
                "code": pattern.get("code"),
                "count": pattern.get("count"),
                "opportunities": pattern.get("opportunities"),
            }
            for pattern in (session.error_patterns or [])
            if isinstance(pattern, dict) and pattern.get("code")
        ],
    }


class DeterministicDecisionSupportEngine:
    """Explainable first engine. It never changes placement or instructional intensity."""

    @transaction.atomic
    def evaluate_completed_session(self, session_id: int) -> list[GrowthFlag]:
        session = (
            Session.objects.select_related(
                "center",
                "child",
                "specialist",
                "curriculum_position__curriculum",
            )
            .filter(pk=session_id, status=Session.Status.COMPLETED, is_deleted=False)
            .first()
        )
        if session is None:
            return []

        position_sessions = list(
            Session.objects.filter(
                child=session.child,
                curriculum_position=session.curriculum_position,
                status=Session.Status.COMPLETED,
                is_deleted=False,
            )
            .select_related("curriculum_position__curriculum")
            .order_by("ended_at", "scheduled_start", "id")
        )
        results = [
            result
            for result in (
                self._three_reteach(session),
                self._flat_accuracy(position_sessions),
                self._mastery_time_outlier(session, position_sessions),
                self._regression_after_mastery(session, position_sessions),
                self._persistent_error_pattern(position_sessions),
                self._attendance_interruption(session, position_sessions),
            )
            if result is not None
        ]
        return [self._store_flag(session, result) for result in results]

    def _three_reteach(self, trigger_session):
        latest = list(
            Session.objects.filter(
                child=trigger_session.child,
                status=Session.Status.COMPLETED,
                is_deleted=False,
            )
            .select_related("curriculum_position")
            .order_by("-ended_at", "-scheduled_start", "-id")[:3]
        )
        latest.reverse()
        if len(latest) < 3:
            return None
        if any(item.curriculum_position_id != trigger_session.curriculum_position_id for item in latest):
            return None
        if not all(bool((item.time_to_mastery_signals or {}).get("reteach")) for item in latest):
            return None
        session_ids = [item.id for item in latest]
        code = trigger_session.curriculum_position.code
        return FlagResult(
            GrowthFlag.Code.THREE_RETEACH_SESSIONS,
            GrowthFlag.Severity.HIGH,
            {"sessions": [_session_evidence(item) for item in latest], "position_code": code},
            f"This flag fired because re-teach was recorded in three consecutive completed sessions at {code} "
            f"(sessions {', '.join(map(str, session_ids))}).",
            "Review item-level patterns before the next session and select a targeted routine; do not change placement automatically.",
        )

    def _flat_accuracy(self, sessions):
        latest = sessions[-4:]
        if len(latest) < 4 or any(_accuracy(item) is None for item in latest):
            return None
        first_accuracy = _accuracy(latest[0])
        last_accuracy = _accuracy(latest[-1])
        gain = round(last_accuracy - first_accuracy, 2)
        if gain >= 5:
            return None
        code = latest[-1].curriculum_position.code
        return FlagResult(
            GrowthFlag.Code.FLAT_ACCURACY,
            GrowthFlag.Severity.MEDIUM,
            {
                "sessions": [_session_evidence(item) for item in latest],
                "position_code": code,
                "first_accuracy": first_accuracy,
                "latest_accuracy": last_accuracy,
                "percentage_point_gain": gain,
                "threshold": 5,
            },
            f"This flag fired because accuracy changed from {first_accuracy:.1f}% to {last_accuracy:.1f}% "
            f"({gain:.1f} percentage points) across four completed captures at {code}, below the 5-point growth threshold.",
            "Review grouping, pacing, prompting, and item variation; any adjustment remains a specialist decision.",
        )

    def _mastery_time_outlier(self, trigger_session, sessions):
        completed_count = len(sessions)
        if completed_count < 4:
            return None
        comparable_counts = self._completed_position_counts(
            trigger_session.curriculum_position.curriculum,
            exclude=(trigger_session.child_id, trigger_session.curriculum_position_id),
        )
        if not comparable_counts:
            return None
        curriculum_median = float(median(comparable_counts))
        threshold = curriculum_median * 1.5
        if completed_count <= threshold:
            return None
        code = trigger_session.curriculum_position.code
        return FlagResult(
            GrowthFlag.Code.MASTERY_TIME_OUTLIER,
            GrowthFlag.Severity.MEDIUM,
            {
                "sessions": [_session_evidence(item) for item in sessions],
                "position_code": code,
                "completed_sessions": completed_count,
                "curriculum_median_sessions": curriculum_median,
                "threshold_sessions": threshold,
                "comparable_position_count": len(comparable_counts),
            },
            f"This flag fired because {completed_count} sessions have been completed at {code}, exceeding "
            f"150% of the curriculum median ({curriculum_median:.1f} sessions) across comparable completed positions.",
            "Review prerequisite evidence and instructional pacing; the system will not move the learner automatically.",
        )

    def _regression_after_mastery(self, trigger_session, sessions):
        position = trigger_session.curriculum_position
        mastery_times = [
            _completed_at(item)
            for item in sessions
            if bool((item.time_to_mastery_signals or {}).get("position_mastered"))
            or bool((item.time_to_mastery_signals or {}).get("mastered"))
        ]
        placement = (
            StudentPlacement.objects.filter(
                child=trigger_session.child,
                curriculum=position.curriculum,
                is_active=True,
                is_deleted=False,
                current_position__sequence_order__gt=position.sequence_order,
            )
            .select_related("current_position")
            .first()
        )
        if placement:
            mastery_times.append(placement.placed_at)
        if not mastery_times:
            return None
        mastered_at = max(mastery_times)
        later_checks = [item for item in sessions if _completed_at(item) > mastered_at and _accuracy(item) is not None]
        if len(later_checks) < 2:
            return None
        latest = later_checks[-2:]
        threshold = self._promotion_threshold(position)
        if not all(_accuracy(item) < threshold for item in latest):
            return None
        code = position.code
        return FlagResult(
            GrowthFlag.Code.REGRESSION_AFTER_MASTERY,
            GrowthFlag.Severity.HIGH,
            {
                "sessions": [_session_evidence(item) for item in latest],
                "position_code": code,
                "mastered_at": mastered_at.isoformat(),
                "promotion_threshold": threshold,
                "accuracies": [_accuracy(item) for item in latest],
            },
            f"This flag fired because two checks after mastery at {code} were below the {threshold:.0f}% "
            f"promotion threshold ({_accuracy(latest[0]):.1f}% and {_accuracy(latest[1]):.1f}%).",
            "Add cumulative review and recheck retained prerequisites; prior mastery remains recorded.",
        )

    def _persistent_error_pattern(self, sessions):
        latest = sessions[-3:]
        if len(latest) < 3:
            return None
        code_sets = [
            {
                pattern.get("code")
                for pattern in (item.error_patterns or [])
                if isinstance(pattern, dict) and pattern.get("code")
            }
            for item in latest
        ]
        persistent = sorted(set.intersection(*code_sets)) if all(code_sets) else []
        if not persistent:
            return None
        pattern_code = persistent[0]
        position_code = latest[-1].curriculum_position.code
        counts = [
            next(
                (
                    pattern.get("count")
                    for pattern in item.error_patterns
                    if isinstance(pattern, dict) and pattern.get("code") == pattern_code
                ),
                None,
            )
            for item in latest
        ]
        return FlagResult(
            GrowthFlag.Code.ERROR_PATTERN_PERSISTENT,
            GrowthFlag.Severity.MEDIUM,
            {
                "sessions": [_session_evidence(item) for item in latest],
                "position_code": position_code,
                "error_code": pattern_code,
                "counts": counts,
            },
            f"This flag fired because the structured error code '{pattern_code}' appeared in three consecutive "
            f"completed captures at {position_code} with counts {counts}.",
            "Select a targeted routine and a new item set; any instructional change requires specialist review.",
        )

    def _attendance_interruption(self, trigger_session, sessions):
        if len(sessions) < 2:
            return None
        placement = StudentPlacement.objects.filter(
            child=trigger_session.child,
            current_position=trigger_session.curriculum_position,
            is_active=True,
            is_deleted=False,
        ).first()
        if placement is None:
            return None
        previous, latest = sessions[-2:]
        gap_days = (_completed_at(latest).date() - _completed_at(previous).date()).days
        if gap_days <= 14:
            return None
        code = trigger_session.curriculum_position.code
        return FlagResult(
            GrowthFlag.Code.ATTENDANCE_INTERRUPTION,
            GrowthFlag.Severity.MEDIUM,
            {
                "sessions": [_session_evidence(previous), _session_evidence(latest)],
                "position_code": code,
                "gap_days": gap_days,
                "threshold_days": 14,
            },
            f"This flag fired because {gap_days} days elapsed between completed sessions while {code} remained in progress, "
            "exceeding the 14-day threshold.",
            "Recheck retained prerequisites at the next session before continuing the planned sequence.",
        )

    @staticmethod
    def _promotion_threshold(position):
        criteria = position.mastery_criteria or {}
        if position.curriculum.code == Curriculum.Code.PFR:
            return float(criteria.get("word_reading_accuracy_percent", 90))
        return float(criteria.get("decoding_accuracy_percent", 90))

    @staticmethod
    def _completed_position_counts(curriculum, exclude=None):
        placements = {
            (placement.child_id, placement.curriculum_id): placement.current_position.sequence_order
            for placement in StudentPlacement.objects.filter(
                curriculum=curriculum,
                is_active=True,
                is_deleted=False,
            ).select_related("current_position")
        }
        grouped = defaultdict(int)
        rows = Session.objects.filter(
            curriculum_position__curriculum=curriculum,
            status=Session.Status.COMPLETED,
            is_deleted=False,
        ).values("child_id", "curriculum_position_id", "curriculum_position__sequence_order")
        for row in rows:
            key = (row["child_id"], row["curriculum_position_id"])
            if exclude and key == exclude:
                continue
            current_order = placements.get((row["child_id"], curriculum.id))
            if current_order is not None and row["curriculum_position__sequence_order"] < current_order:
                grouped[key] += 1
        return list(grouped.values())

    def _store_flag(self, trigger_session, result):
        flag, created = GrowthFlag.objects.update_or_create(
            center=trigger_session.center,
            child=trigger_session.child,
            position=trigger_session.curriculum_position,
            flag_code=result.code,
            status=GrowthFlag.Status.OPEN,
            defaults={
                "trigger_session": trigger_session,
                "severity": result.severity,
                "evidence_snapshot": {
                    "engine_version": ENGINE_VERSION,
                    "generated_at": timezone.now().isoformat(),
                    **result.evidence,
                },
                "explanation": result.explanation,
                "advisory_recommendation": result.advisory_recommendation,
            },
        )
        routed_users = {trigger_session.specialist}
        if result.severity == GrowthFlag.Severity.HIGH:
            leadership_roles = getattr(
                settings,
                "DECISION_SUPPORT_LEADERSHIP_ROLES",
                [SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN],
            )
            routed_users.update(
                membership.user
                for membership in SchoolMembership.objects.filter(
                    school=trigger_session.center,
                    role__in=leadership_roles,
                    is_deleted=False,
                    user__is_active=True,
                ).select_related("user")
            )
        flag.routed_to.set(routed_users)
        if created and result.severity == GrowthFlag.Severity.HIGH:
            from apps.decision_support.tasks import notify_high_growth_flag

            transaction.on_commit(lambda: notify_high_growth_flag.delay(flag.id))
        return flag

    @transaction.atomic
    def generate_milestone_prediction(self, child_id: int) -> MilestonePrediction:
        placement = (
            StudentPlacement.objects.select_related(
                "center",
                "child",
                "curriculum",
                "current_position",
            )
            .filter(child_id=child_id, is_active=True, is_deleted=False)
            .first()
        )
        if placement is None:
            raise ValueError("An active placement is required for milestone prediction.")

        positions = list(placement.curriculum.positions.filter(is_deleted=False).order_by("sequence_order"))
        if not positions:
            raise ValueError("The active curriculum has no sequence positions.")
        target = positions[-1]
        remaining_positions = sum(
            position.sequence_order >= placement.current_position.sequence_order
            for position in positions
        )

        all_completed_counts = self._completed_position_counts(placement.curriculum)
        child_completed_counts = self._child_completed_position_counts(placement)
        if child_completed_counts:
            sessions_per_position = float(median(child_completed_counts))
            source = "child_history"
            sample_size = len(child_completed_counts)
        elif all_completed_counts:
            sessions_per_position = float(median(all_completed_counts))
            source = "comparable_curriculum_history"
            sample_size = len(all_completed_counts)
        else:
            sessions_per_position = 2.0
            source = "methodology_two_check_default"
            sample_size = 0

        predicted_sessions = ceil(remaining_positions * sessions_per_position)
        recent_cutoff = timezone.now() - timedelta(weeks=8)
        recent_sessions = list(
            Session.objects.filter(
                child=placement.child,
                curriculum_position__curriculum=placement.curriculum,
                status=Session.Status.COMPLETED,
                is_deleted=False,
                scheduled_start__gte=recent_cutoff,
            )
            .order_by("scheduled_start")
            .values_list("id", "scheduled_start")
        )
        observed_weekly_rate = len(recent_sessions) / 8
        weekly_rate = observed_weekly_rate if observed_weekly_rate > 0 else 1.0
        predicted_weeks = ceil(predicted_sessions / weekly_rate) if predicted_sessions else 0
        predicted_date = timezone.localdate() + timedelta(weeks=predicted_weeks)

        if len(child_completed_counts) >= 3:
            confidence = MilestonePrediction.Confidence.HIGH
            lower_factor, upper_factor = 0.75, 1.35
        elif sample_size >= 3:
            confidence = MilestonePrediction.Confidence.MEDIUM
            lower_factor, upper_factor = 0.65, 1.55
        else:
            confidence = MilestonePrediction.Confidence.LOW
            lower_factor, upper_factor = 0.5, 2.0
        lower_bound = max(0, ceil(predicted_sessions * lower_factor))
        upper_bound = max(lower_bound, ceil(predicted_sessions * upper_factor))
        explanation = (
            f"The estimate uses {sessions_per_position:.1f} sessions per completed position from {source.replace('_', ' ')} "
            f"and a recent attendance rate of {weekly_rate:.2f} sessions per week."
        )
        parent_timeline = (
            f"At the recent pace, sequence completion is estimated around {predicted_date.strftime('%B %Y')}, "
            f"with roughly {lower_bound} to {upper_bound} more sessions. This range can change as new progress is recorded."
        )
        evidence = {
            "engine_version": ENGINE_VERSION,
            "methodology": placement.curriculum.code,
            "curriculum_version": placement.curriculum.version,
            "current_position": placement.current_position.code,
            "target_position": target.code,
            "positions_in_estimate": remaining_positions,
            "sessions_per_position": sessions_per_position,
            "sessions_per_position_source": source,
            "completed_position_sample_size": sample_size,
            "child_completed_position_counts": child_completed_counts,
            "comparable_completed_position_counts": all_completed_counts,
            "recent_session_ids": [row[0] for row in recent_sessions],
            "observed_weekly_session_rate": round(observed_weekly_rate, 2),
            "planning_weekly_session_rate": round(weekly_rate, 2),
        }
        MilestonePrediction.objects.filter(child=placement.child, is_current=True).update(is_current=False)
        return MilestonePrediction.objects.create(
            center=placement.center,
            child=placement.child,
            placement=placement,
            target_position=target,
            target_label="Current sequence completion",
            predicted_sessions=predicted_sessions,
            predicted_date=predicted_date,
            lower_bound_sessions=lower_bound,
            upper_bound_sessions=upper_bound,
            confidence=confidence,
            evidence_summary=evidence,
            explanation=explanation,
            parent_timeline=parent_timeline,
            engine_version=ENGINE_VERSION,
        )

    @staticmethod
    def _child_completed_position_counts(placement):
        counts = Counter()
        rows = Session.objects.filter(
            child=placement.child,
            curriculum_position__curriculum=placement.curriculum,
            curriculum_position__sequence_order__lt=placement.current_position.sequence_order,
            status=Session.Status.COMPLETED,
            is_deleted=False,
        ).order_by(
            "curriculum_position__sequence_order",
            "id",
        ).values_list("curriculum_position_id", flat=True)
        counts.update(rows)
        return list(counts.values())
