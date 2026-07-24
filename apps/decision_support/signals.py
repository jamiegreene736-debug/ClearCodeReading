from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.decision_support.tasks import evaluate_completed_session
from apps.sessions.models import Session


@receiver(post_save, sender=Session)
def evaluate_newly_completed_session(sender, instance, **kwargs):
    previous_status = getattr(instance, "_previous_status", None)
    if instance.status == Session.Status.COMPLETED and previous_status != Session.Status.COMPLETED:
        transaction.on_commit(
            lambda: evaluate_completed_session.apply_async(
                args=[instance.id],
                ignore_result=True,
                retry=False,
            ),
            robust=True,
        )
