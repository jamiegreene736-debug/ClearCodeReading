from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from apps.assessments.models import Assessment
from apps.notifications.tasks import (
    notify_evaluator_assessment_human_review,
    send_consent_request_notification,
    send_progress_report_to_parents,
)
from apps.users.models import AuditLog, ConsentLog, GuardianRelationship


@receiver(pre_save, sender=Assessment)
def cache_assessment_previous_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_status = None
        return
    instance._previous_status = (
        Assessment.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
    )


@receiver(post_save, sender=Assessment)
def handle_assessment_status_change(sender, instance, created, **kwargs):
    previous_status = getattr(instance, "_previous_status", None)
    if instance.status == Assessment.Status.HUMAN_REVIEW and previous_status != Assessment.Status.HUMAN_REVIEW:
        transaction.on_commit(lambda: notify_evaluator_assessment_human_review.delay(instance.id))
        AuditLog.objects.create(
            actor=instance.assigned_by,
            action="assessment.status.human_review",
            entity_type="Assessment",
            entity_id=str(instance.id),
            before={"status": previous_status},
            after={"status": instance.status},
        )

    if instance.status == Assessment.Status.COMPLETED and previous_status != Assessment.Status.COMPLETED:
        transaction.on_commit(lambda: send_progress_report_to_parents.delay(instance.child_id))
        AuditLog.objects.create(
            actor=instance.assigned_by,
            action="assessment.status.completed",
            entity_type="Assessment",
            entity_id=str(instance.id),
            before={"status": previous_status},
            after={"status": instance.status, "completed_at": instance.completed_at.isoformat() if instance.completed_at else None},
        )


@receiver(post_save, sender=GuardianRelationship)
def handle_guardian_relationship_created(sender, instance, created, **kwargs):
    if created and instance.consent_status == GuardianRelationship.ConsentStatus.PENDING:
        transaction.on_commit(lambda: send_consent_request_notification.delay(instance.id, channels=["email", "sms"]))


@receiver(post_save, sender=ConsentLog)
def handle_consent_log_created(sender, instance, created, **kwargs):
    if not created or instance.guardian_relationship_id is None:
        return

    relationship = instance.guardian_relationship
    status_map = {
        ConsentLog.Status.GRANTED: GuardianRelationship.ConsentStatus.GRANTED,
        ConsentLog.Status.REVOKED: GuardianRelationship.ConsentStatus.REVOKED,
        ConsentLog.Status.EXPIRED: GuardianRelationship.ConsentStatus.EXPIRED,
    }
    relationship.consent_status = status_map[instance.status]
    relationship.consent_expires_at = instance.expires_at
    relationship.save(update_fields=["consent_status", "consent_expires_at", "updated_at"])

    AuditLog.objects.create(
        actor=instance.guardian,
        action=f"consent.{instance.status}",
        entity_type="ConsentLog",
        entity_id=str(instance.id),
        after={
            "relationship_id": relationship.id,
            "child_id": instance.child_id,
            "consent_type": instance.consent_type,
            "status": instance.status,
            "expires_at": instance.expires_at.isoformat() if instance.expires_at else None,
            "logged_at": timezone.now().isoformat(),
        },
    )
