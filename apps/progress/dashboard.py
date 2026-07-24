from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.curriculum.models import StudentPlacement
from apps.decision_support.models import MilestonePrediction
from apps.progress.models import MasteryRecord, Progress
from apps.sessions.models import Session


def _number(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


def _session_wcpm(session):
    direct = (session.time_to_mastery_signals or {}).get("wcpm")
    if isinstance(direct, (int, float)):
        return direct
    for item_set in (session.item_sets or {}).values():
        if isinstance(item_set, dict) and isinstance(item_set.get("wcpm"), (int, float)):
            return item_set["wcpm"]
    return None


def _decodable_result(session):
    for key, item_set in (session.item_sets or {}).items():
        if not isinstance(item_set, dict):
            continue
        if item_set.get("type") == "decodable_text" or "decodable" in key.lower():
            return {
                "session_id": session.id,
                "date": session.ended_at.date() if session.ended_at else session.scheduled_start.date(),
                "title": item_set.get("title") or item_set.get("text_title") or "Decodable text",
                "accuracy": item_set.get("accuracy", _number(session.accuracy_rate)),
                "completed": item_set.get("completed", True),
            }
    if any("decodable" in str(activity.get("code", "")).lower() for activity in session.activities_completed if isinstance(activity, dict)):
        return {
            "session_id": session.id,
            "date": session.ended_at.date() if session.ended_at else session.scheduled_start.date(),
            "title": "Decodable-text practice",
            "accuracy": _number(session.accuracy_rate),
            "completed": True,
        }
    return None


def build_parent_dashboard(child):
    progress = list(
        Progress.objects.filter(child=child, is_deleted=False)
        .select_related("skill")
        .order_by("skill__domain", "skill__code")
    )
    sessions = list(
        Session.objects.filter(child=child, status=Session.Status.COMPLETED, is_deleted=False)
        .select_related("specialist", "curriculum_position__curriculum")
        .prefetch_related("targeted_positions")
        .order_by("ended_at", "scheduled_start")
    )
    placement = (
        StudentPlacement.objects.filter(child=child, is_active=True, is_deleted=False)
        .select_related("curriculum", "current_position")
        .first()
    )
    mastery = list(
        MasteryRecord.objects.filter(child=child, is_deleted=False)
        .select_related("skill")
        .order_by("-mastered_at")[:10]
    )
    latest = sessions[-1] if sessions else None
    fluency = [
        {
            "session_id": session.id,
            "date": session.ended_at.date() if session.ended_at else session.scheduled_start.date(),
            "wcpm": wcpm,
        }
        for session in sessions
        if (wcpm := _session_wcpm(session)) is not None
    ]
    decodable = [result for session in sessions if (result := _decodable_result(session))]
    chart = [
        {
            "session_id": session.id,
            "date": session.ended_at.date() if session.ended_at else session.scheduled_start.date(),
            "accuracy": _number(session.accuracy_rate),
            "wcpm": _session_wcpm(session),
        }
        for session in sessions[-12:]
    ]

    milestone = {"status": "not_available", "label": "Milestone estimate will appear after placement."}
    prediction = (
        MilestonePrediction.objects.filter(child=child, is_current=True)
        .select_related("placement__current_position", "target_position")
        .first()
    )
    if prediction:
        milestone = prediction.parent_payload()
    elif placement:
        total = placement.curriculum.positions.filter(is_deleted=False).count()
        remaining = max(total - placement.current_position.sequence_order, 0)
        recent_cutoff = timezone.now() - timedelta(weeks=8)
        recent_count = sum(1 for session in sessions if session.scheduled_start >= recent_cutoff)
        weekly_rate = max(recent_count / 8, 1)
        weeks = round(remaining / weekly_rate) if remaining else 0
        milestone = {
            "status": "estimate",
            "label": "Current sequence completion",
            "current_position": placement.current_position.code,
            "positions_remaining": remaining,
            "estimated_weeks": weeks,
            "estimated_date": timezone.localdate() + timedelta(weeks=weeks),
            "disclaimer": "Simple estimate based on current sequence position and recent attendance; it is not a guarantee.",
        }

    return {
        "child": {"id": child.id, "first_name": child.first_name, "display_name": str(child)},
        "generated_at": timezone.now(),
        "week_start": timezone.localdate() - timedelta(days=timezone.localdate().weekday()),
        "summary": {
            "tracked_skills": len(progress),
            "mastered_skills": sum(record.status == Progress.Status.MASTERED for record in progress),
            "completed_sessions": len(sessions),
            "latest_accuracy": _number(latest.accuracy_rate) if latest else None,
        },
        "skills": [
            {
                "id": record.skill_id,
                "code": record.skill.code,
                "name": record.skill.name,
                "domain": record.skill.domain,
                "status": record.status,
                "score": _number(record.current_score),
                "updated_at": record.updated_at,
            }
            for record in progress
        ],
        "recent_mastery": [
            {"skill": record.skill.name, "code": record.skill.code, "mastered_at": record.mastered_at, "score": _number(record.score)}
            for record in mastery
        ],
        "fluency_trend": fluency[-12:],
        "decodable_text_progress": decodable[-8:],
        "progress_over_time": chart,
        "specialist_note": (
            (latest.notes or latest.next_session_direction).strip() if latest else ""
        ),
        "specialist_name": latest.specialist.get_full_name() or latest.specialist.email if latest else "",
        "home_practice": latest.home_practice_suggestion.strip() if latest else "",
        "milestone": milestone,
        "foundational_skills_only": True,
    }
