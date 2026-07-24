from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from apps.curriculum.models import CurriculumSequence
from apps.sessions.models import Session, SessionTemplate, SkillObservation


def resolve_session_template(
    curriculum_position: CurriculumSequence,
    session_part: str,
) -> SessionTemplate | None:
    """Return the newest active exact-position template, then a curriculum fallback."""
    return (
        SessionTemplate.objects.filter(
            center_id=curriculum_position.center_id,
            curriculum_id=curriculum_position.curriculum_id,
            session_part=session_part,
            is_active=True,
            is_deleted=False,
        )
        .filter(Q(curriculum_position=curriculum_position) | Q(curriculum_position__isnull=True))
        .annotate(
            position_priority=Case(
                When(curriculum_position=curriculum_position, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("position_priority", "-version", "-updated_at")
        .first()
    )


def capture_defaults(template: SessionTemplate | None) -> dict:
    """Return independent mutable defaults suitable for a new capture form."""
    if template is None:
        return {}
    return {
        field_name: deepcopy(config["default"])
        for field_name, config in template.capture_fields.get("properties", {}).items()
        if isinstance(config, dict) and "default" in config
    }


def apply_template_defaults(values: dict, template: SessionTemplate | None) -> None:
    for field_name, default in capture_defaults(template).items():
        if hasattr(Session, field_name):
            values.setdefault(field_name, default)


def _flatten_item_references(session: Session) -> list[dict]:
    references = []
    for section_code, item_set in session.item_sets.items():
        if not isinstance(item_set, dict):
            continue
        item_set_id = item_set.get("item_set_id")
        for item in item_set.get("items", []):
            if not isinstance(item, dict):
                continue
            references.append(
                {
                    "section_code": section_code,
                    "item_set_id": item_set_id,
                    **{
                        key: item[key]
                        for key in (
                            "item_id",
                            "position_code",
                            "correct",
                            "mode",
                            "prompt_level",
                            "latency_seconds",
                            "timeout",
                            "response_rating",
                        )
                        if key in item
                    },
                }
            )
    return references


def _accuracy_from_items(items: list[dict]) -> Decimal | None:
    scorable = [item for item in items if isinstance(item.get("correct"), bool)]
    if not scorable:
        return None
    correct = sum(item["correct"] for item in scorable)
    return (Decimal(correct * 100) / Decimal(len(scorable))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _response_rating_from_items(items: list[dict]) -> int | None:
    ratings = [
        item["response_rating"]
        for item in items
        if isinstance(item.get("response_rating"), int)
        and not isinstance(item["response_rating"], bool)
        and 1 <= item["response_rating"] <= 5
    ]
    if not ratings:
        return None
    average = (Decimal(sum(ratings)) / Decimal(len(ratings))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return max(1, min(5, int(average)))


def _derived_time_signals(items: list[dict]) -> dict:
    latencies = [
        Decimal(str(item["latency_seconds"]))
        for item in items
        if isinstance(item.get("latency_seconds"), (int, float))
        and not isinstance(item["latency_seconds"], bool)
        and item["latency_seconds"] >= 0
    ]
    signals = {
        "item_count": len(items),
        "timed_item_count": len(latencies),
        "timeout_count": sum(bool(item.get("timeout")) for item in items),
    }
    if latencies:
        signals["average_latency_seconds"] = float(
            (sum(latencies) / Decimal(len(latencies))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
    return signals


@transaction.atomic
def sync_skill_observations(session: Session) -> list[SkillObservation]:
    """Project a session's structured capture into canonical queryable evidence."""
    existing = SkillObservation.objects.select_for_update().filter(session=session)
    if session.status != Session.Status.COMPLETED or session.is_deleted:
        for observation in existing.filter(is_deleted=False):
            observation.is_deleted = True
            observation.deleted_at = session.deleted_at or timezone.now()
            observation.updated_by = session.updated_by
            observation.save(update_fields=["is_deleted", "deleted_at", "updated_by", "updated_at"])
        return []

    position_ids = [session.curriculum_position_id]
    position_ids.extend(
        session.targeted_positions.exclude(pk=session.curriculum_position_id).values_list("pk", flat=True)
    )
    positions = {
        position.pk: position
        for position in CurriculumSequence.objects.filter(pk__in=position_ids).select_related("curriculum")
    }
    item_references = _flatten_item_references(session)
    active_observations = []

    for position_id in position_ids:
        position = positions[position_id]
        is_primary = position_id == session.curriculum_position_id
        position_items = [
            item
            for item in item_references
            if item.get("position_code") == position.code
            or (is_primary and not item.get("position_code"))
        ]
        position_activities = [
            deepcopy(activity)
            for activity in session.activities_completed
            if isinstance(activity, dict)
            and (
                activity.get("position_code") == position.code
                or (is_primary and not activity.get("position_code"))
            )
        ]
        position_errors = [
            pattern
            for pattern in session.error_patterns
            if isinstance(pattern, dict)
            and (
                pattern.get("position_code") == position.code
                or (is_primary and not pattern.get("position_code"))
            )
        ]
        error_tags = list(dict.fromkeys(pattern["code"] for pattern in position_errors if pattern.get("code")))
        accuracy_rate = session.accuracy_rate if is_primary else _accuracy_from_items(position_items)
        time_signals = _derived_time_signals(position_items)
        if is_primary:
            time_signals = {**deepcopy(session.time_to_mastery_signals), **time_signals}

        observation = existing.filter(curriculum_position=position).first()
        if observation is None:
            observation = SkillObservation(
                session=session,
                child=session.child,
                curriculum_position=position,
                center=session.center,
                created_by=session.created_by,
            )
        observation.accuracy_rate = accuracy_rate
        observation.response_rating = _response_rating_from_items(position_items)
        observation.error_pattern_tags = error_tags
        observation.time_signals = time_signals
        observation.activities = position_activities
        observation.item_references = position_items
        observation.source_session_revision = session.revision
        observation.metadata = {
            "source": "session_structured_capture",
            "intervention_part": session.intervention_part,
            "session_template_id": session.session_template_id,
        }
        observation.updated_by = session.updated_by or session.created_by
        observation.is_deleted = False
        observation.deleted_at = None
        observation.full_clean()
        observation.save()
        active_observations.append(observation)

    for observation in existing.exclude(curriculum_position_id__in=position_ids).filter(is_deleted=False):
        observation.soft_delete()
    return active_observations
