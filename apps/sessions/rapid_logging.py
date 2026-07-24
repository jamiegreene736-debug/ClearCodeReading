import re
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.ai.services import InstructionalAIError, get_instructional_ai_service
from apps.curriculum.models import StudentPlacement
from apps.sessions.models import Session
from apps.sessions.options import (
    BEHAVIORAL_CODES,
    BEHAVIORAL_OBSERVATION_OPTIONS,
    BEHAVIORAL_RATING_OPTIONS,
    BEHAVIORAL_RATINGS,
    ERROR_PATTERN_CODES,
    ERROR_PATTERN_LABELS,
    ERROR_PATTERN_OPTIONS,
)
from apps.sessions.services import capture_defaults, resolve_session_template


def default_intervention_part(child, position):
    if position.curriculum.code == "og_plus":
        return Session.InterventionPart.OG_CONCEPT
    latest = (
        Session.objects.filter(
            child=child,
            curriculum_position=position,
            status=Session.Status.COMPLETED,
            is_deleted=False,
        )
        .order_by("-ended_at", "-created_at")
        .first()
    )
    if latest and latest.intervention_part == Session.InterventionPart.PFR_1A:
        return Session.InterventionPart.PFR_1B
    return Session.InterventionPart.PFR_1A


def get_active_placement(child):
    placement = (
        StudentPlacement.objects.filter(child=child, is_active=True, is_deleted=False)
        .select_related("center", "curriculum", "current_position__curriculum")
        .first()
    )
    if placement is None:
        raise ValidationError("This reader does not have an active placement.")
    return placement


def build_rapid_defaults(child, specialist, session=None):
    placement = get_active_placement(child)
    position = session.curriculum_position if session else placement.current_position
    part = session.intervention_part if session else default_intervention_part(child, position)
    session_template = resolve_session_template(position, part)
    activity_codes = _activity_codes(position, part)
    item_set_ids = _next_item_set_ids(child, position, part, activity_codes, session)
    latest_accuracy = (
        Session.objects.filter(
            child=child,
            curriculum_position=position,
            status=Session.Status.COMPLETED,
            is_deleted=False,
        )
        .exclude(pk=getattr(session, "pk", None))
        .order_by("-ended_at", "-created_at")
        .values_list("accuracy_rate", flat=True)
        .first()
    )
    direction, practice, metadata = draft_session_suggestions(
        position=position,
        intervention_part=part,
        accuracy_rate=latest_accuracy,
        error_pattern_codes=[],
    )
    return {
        "child": child.id,
        "child_name": str(child),
        "center": session.center_id if session else placement.center_id,
        "curriculum": position.curriculum_id,
        "methodology": position.curriculum.code,
        "curriculum_name": position.curriculum.name,
        "curriculum_position": position.id,
        "position_code": position.code,
        "position_title": position.title,
        "targeted_positions": [position.id],
        "intervention_part": part,
        "intervention_part_label": Session.InterventionPart(part).label,
        "session_template": session_template.id if session_template else None,
        "session_template_title": session_template.title if session_template else None,
        "session_template_version": session_template.version if session_template else None,
        "capture_fields": session_template.capture_fields if session_template else {},
        "capture_defaults": capture_defaults(session_template),
        "scheduled_start": session.scheduled_start if session else timezone.now(),
        "suggested_activity_codes": activity_codes,
        "suggested_activities": [
            {"code": code, "label": code.replace("_", " ").title()} for code in activity_codes
        ],
        "expected_item_set_ids": item_set_ids,
        "item_set_schema": position.item_set_schema,
        "mastery_criteria": position.mastery_criteria,
        "behavioral_observation_options": [
            {"code": code, "label": label} for code, label in BEHAVIORAL_OBSERVATION_OPTIONS
        ],
        "behavioral_rating_options": [
            {"code": code, "label": label} for code, label in BEHAVIORAL_RATING_OPTIONS
        ],
        "error_pattern_options": [
            {"code": code, "label": label} for code, label in ERROR_PATTERN_OPTIONS
        ],
        "next_session_direction": session.next_session_direction if session else direction,
        "home_practice_suggestion": session.home_practice_suggestion if session else practice,
        "suggestion_metadata": metadata,
        "session": session.id if session else None,
        "specialist": specialist.id,
    }


def expand_quick_complete(
    *,
    child,
    specialist,
    accuracy_numerator,
    accuracy_denominator,
    duration_minutes=60,
    scheduled_start=None,
    activity_codes=None,
    error_pattern_codes=None,
    behavioral_observation_codes=None,
    behavioral_rating="consistent",
    next_session_direction="",
    home_practice_suggestion="",
    notes="",
    session=None,
):
    defaults = build_rapid_defaults(child, specialist, session)
    position = session.curriculum_position if session else get_active_placement(child).current_position
    activity_codes = _validated_codes(
        activity_codes or defaults["suggested_activity_codes"],
        set(defaults["suggested_activity_codes"]),
        "activity_codes",
    )
    error_pattern_codes = _validated_codes(
        error_pattern_codes or [], ERROR_PATTERN_CODES, "error_pattern_codes"
    )
    behavioral_observation_codes = _validated_codes(
        behavioral_observation_codes or [], BEHAVIORAL_CODES, "behavioral_observation_codes"
    )
    if behavioral_rating not in BEHAVIORAL_RATINGS:
        raise ValidationError({"behavioral_rating": "Select an allowed observable rating."})
    rate = (Decimal(accuracy_numerator) * 100 / Decimal(accuracy_denominator)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    ended_at = timezone.now()
    started_at = ended_at - timezone.timedelta(minutes=duration_minutes)
    item_set_ids = defaults["expected_item_set_ids"]
    primary = activity_codes[0]
    aggregate = {
        "item_id": f"{item_set_ids[primary]}-aggregate",
        "correct": accuracy_numerator == accuracy_denominator,
        "mode": "aggregate",
        "prompt_level": "not_recorded",
    }
    aggregate["latency_seconds" if position.curriculum.code == "pfr" else "position_code"] = (
        None if position.curriculum.code == "pfr" else position.code
    )
    prior = Session.objects.filter(
        child=child,
        curriculum_position=position,
        status=Session.Status.COMPLETED,
        is_deleted=False,
    )
    if session:
        prior = prior.exclude(pk=session.pk)
    first_accuracy = prior.order_by("ended_at", "created_at").values_list("accuracy_rate", flat=True).first()
    direction, practice, _ = draft_session_suggestions(
        position=position,
        intervention_part=defaults["intervention_part"],
        accuracy_rate=rate,
        error_pattern_codes=error_pattern_codes,
    )
    return {
        "child": child.id,
        "status": Session.Status.COMPLETED,
        "curriculum_position": position.id,
        "targeted_positions": [position.id],
        "intervention_part": defaults["intervention_part"],
        "scheduled_start": scheduled_start or (session.scheduled_start if session else started_at),
        "started_at": started_at,
        "ended_at": ended_at,
        "activities_completed": [
            {
                "code": code,
                "status": "completed",
                "minutes": max(1, duration_minutes // len(activity_codes)),
                "item_set_id": item_set_ids[code],
            }
            for code in activity_codes
        ],
        "item_sets": {
            primary: {
                "item_set_id": item_set_ids[primary],
                "correct": accuracy_numerator,
                "total": accuracy_denominator,
                "aggregate_only": True,
                "items": [aggregate],
            }
        },
        "accuracy_numerator": accuracy_numerator,
        "accuracy_denominator": accuracy_denominator,
        "accuracy_rate": rate,
        "time_to_mastery_signals": {
            "cumulative_sessions_at_position": prior.count() + 1,
            "first_attempt_accuracy": float(first_accuracy if first_accuracy is not None else rate),
            "latest_accuracy": float(rate),
            "prompts_per_10_items": 0,
            "independent_transfer": rate >= 90,
            "reteach": rate < 80 or bool(error_pattern_codes),
        },
        "error_patterns": [
            {"code": code, "count": 1, "opportunities": accuracy_denominator, "capture_mode": "quick_complete"}
            for code in error_pattern_codes
        ],
        "behavioral_observations": [
            {"code": code, "rating": behavioral_rating, "activity_code": primary}
            for code in behavioral_observation_codes
        ],
        "next_session_direction": next_session_direction.strip() or direction,
        "home_practice_suggestion": home_practice_suggestion.strip() or practice,
        "notes": notes.strip(),
    }


def draft_session_suggestions(*, position, intervention_part, accuracy_rate, error_pattern_codes):
    accuracy = Decimal(str(accuracy_rate)) if accuracy_rate is not None else None
    pattern_text = ", ".join(
        ERROR_PATTERN_LABELS[code].lower()
        for code in error_pattern_codes[:2]
        if code in ERROR_PATTERN_LABELS
    )
    if accuracy is None:
        direction = f"Continue {position.code} using the next distinct item set."
        practice = "Practice five assigned items once with accurate, unhurried reading."
    elif accuracy >= 90 and not error_pattern_codes:
        direction = (
            f"Continue {position.code} with PFR Session 1b and a distinct application set."
            if intervention_part == Session.InterventionPart.PFR_1A
            else f"Check retention for {position.code}, then advance when promotion criteria are met."
        )
        practice = "Read and spell five successful items once, then reread one familiar sentence."
    elif accuracy >= 80:
        direction = f"Repeat the affected {position.code} routine with a fresh item set"
        direction += f" focused on {pattern_text}." if pattern_text else "."
        practice = "Practice three accurate examples from today, then read each in a short phrase."
    else:
        direction = f"Re-teach {position.code} explicitly with fewer items and immediate corrective feedback"
        direction += f" for {pattern_text}." if pattern_text else "."
        practice = "Practice two familiar examples accurately; stop before effort becomes inconsistent."
    metadata = {"source": "deterministic", "advisory": True, "editable": True}
    if not getattr(settings, "INSTRUCTIONAL_AI_ALLOW_NARRATIVE", False):
        return direction, practice, metadata
    try:
        output = get_instructional_ai_service().session_suggestions(
            {
                "position_code": position.code,
                "methodology": position.curriculum.code,
                "intervention_part": intervention_part,
                "accuracy_rate": float(accuracy) if accuracy is not None else None,
                "error_pattern_codes": list(error_pattern_codes),
            }
        )
    except (InstructionalAIError, AttributeError):
        output = None
    if output is None:
        return direction, practice, metadata
    return output.next_session_direction, output.home_practice_suggestion, {
        "source": "instructional_ai",
        "provider": output.provider,
        "model": output.model,
        "advisory": True,
        "editable": True,
    }


def _activity_codes(position, part):
    schema = position.item_set_schema if isinstance(position.item_set_schema, dict) else {}
    key = {
        Session.InterventionPart.PFR_1A: "session_1a",
        Session.InterventionPart.PFR_1B: "session_1b",
        Session.InterventionPart.OG_CONCEPT: "required",
    }[part]
    candidates = schema.get(key, [])
    if not isinstance(candidates, list) or not candidates:
        candidates = position.activities if isinstance(position.activities, list) else []
    result = []
    for candidate in candidates:
        code = candidate.get("code") if isinstance(candidate, dict) else candidate
        code = str(code).strip().lower().replace(" ", "_")
        if code and code not in result:
            result.append(code)
    return result or ["guided_practice"]


def _next_item_set_ids(child, position, part, activity_codes, exclude_session=None):
    used = set()
    sessions = Session.objects.filter(child=child, curriculum_position=position, is_deleted=False)
    if exclude_session:
        sessions = sessions.exclude(pk=exclude_session.pk)
    for prior in sessions.only("activities_completed", "item_sets"):
        used.update(
            activity.get("item_set_id")
            for activity in prior.activities_completed
            if isinstance(activity, dict) and activity.get("item_set_id")
        )
        used.update(
            value.get("item_set_id")
            for value in prior.item_sets.values()
            if isinstance(value, dict) and value.get("item_set_id")
        )
    part_token = {
        Session.InterventionPart.PFR_1A: "1A",
        Session.InterventionPart.PFR_1B: "1B",
        Session.InterventionPart.OG_CONCEPT: "OG",
    }[part]
    run = 1
    while True:
        result = {
            code: f"{position.code}-{part_token}-{run:02d}-{_token(code)}" for code in activity_codes
        }
        if not set(result.values()) & used:
            return result
        run += 1


def _validated_codes(values, allowed, field_name):
    values = list(dict.fromkeys(values))
    invalid = sorted(set(values) - set(allowed))
    if invalid:
        raise ValidationError({field_name: f"Unsupported codes: {', '.join(invalid)}."})
    if field_name == "activity_codes" and not values:
        raise ValidationError({field_name: "Select at least one activity."})
    return values


def _token(value):
    return re.sub(r"[^A-Z0-9]+", "-", value.upper()).strip("-")[:18] or "ITEMS"
