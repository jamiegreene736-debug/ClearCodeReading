from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.curriculum.models import StudentPlacement
from apps.scheduling.models import GroupMembership


@receiver(post_save, sender=StudentPlacement)
def remove_incompatible_group_memberships(sender, instance, **kwargs):
    """Keep active groups methodology-safe when a placement changes."""

    if not instance.is_active or instance.is_deleted:
        return
    memberships = GroupMembership.objects.filter(
        child_id=instance.child_id,
        group__is_active=True,
    )
    incompatible = (
        ~Q(group__curriculum_id=instance.curriculum_id)
        | Q(group__sequence_start__sequence_order__gt=instance.current_position.sequence_order)
        | Q(group__sequence_end__sequence_order__lt=instance.current_position.sequence_order)
    )
    memberships.filter(incompatible).delete()
