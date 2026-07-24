from decimal import Decimal

from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from .models import Session, SessionRevision


SNAPSHOT_FIELDS = (
    "center_id",
    "child_id",
    "specialist_id",
    "curriculum_position_id",
    "status",
    "intervention_part",
    "scheduled_start",
    "started_at",
    "ended_at",
    "activities_completed",
    "item_sets",
    "accuracy_rate",
    "accuracy_numerator",
    "accuracy_denominator",
    "time_to_mastery_signals",
    "error_patterns",
    "behavioral_observations",
    "next_session_direction",
    "home_practice_suggestion",
    "notes",
    "is_deleted",
    "deleted_at",
)


def _serialize_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if isinstance(value, Decimal) else value


@receiver(post_save, sender=Session)
def snapshot_session_revision(sender, instance, **kwargs):
    snapshot = {
        field: _serialize_value(getattr(instance, field))
        for field in SNAPSHOT_FIELDS
    }
    snapshot["targeted_position_ids"] = list(
        instance.targeted_positions.order_by("sequence_order").values_list("id", flat=True)
    )
    SessionRevision.objects.update_or_create(
        session=instance,
        revision=instance.revision,
        defaults={
            "center": instance.center,
            "changed_by": instance.updated_by or instance.created_by,
            "snapshot": snapshot,
        },
    )


@receiver(m2m_changed, sender=Session.targeted_positions.through)
def snapshot_targeted_positions(sender, instance, action, **kwargs):
    if action not in {"post_add", "post_remove", "post_clear"}:
        return
    revision = SessionRevision.objects.filter(session=instance, revision=instance.revision).first()
    if revision is None:
        return
    revision.snapshot = {
        **revision.snapshot,
        "targeted_position_ids": list(instance.targeted_positions.order_by("sequence_order").values_list("id", flat=True)),
    }
    revision.save(update_fields=["snapshot", "updated_at"])
