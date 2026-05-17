# Clear Code Reading Async Layer

## `apps/notifications/__init__.py`
```python


```

## `apps/notifications/apps.py`
```python
from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"

    def ready(self):
        from . import signals  # noqa: F401

```

## `apps/notifications/services.py`
```python
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from apps.assessments.models import Assessment
from apps.progress.models import Progress
from apps.schools.models import SchoolMembership
from apps.users.models import AuditLog, ConsentLog, CustomUser, GuardianRelationship


class NotificationService:
    default_from_email = "Clear Code Reading <no-reply@clearcodereading.com>"

    def send_email(self, subject, message, recipients, html_message=None):
        recipients = [recipient for recipient in recipients if recipient]
        if not recipients:
            return {"sent": 0, "channel": "email"}

        sent_count = send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", self.default_from_email),
            recipient_list=recipients,
            fail_silently=getattr(settings, "NOTIFICATIONS_FAIL_SILENTLY", True),
            html_message=html_message,
        )
        return {"sent": sent_count, "channel": "email", "recipients": recipients}

    def send_sms(self, message, phone_numbers):
        phone_numbers = [phone_number for phone_number in phone_numbers if phone_number]
        for phone_number in phone_numbers:
            AuditLog.objects.create(
                action="notification.sms.queued",
                entity_type="PhoneNumber",
                entity_id=phone_number,
                after={"message": message[:240]},
                metadata={"provider": getattr(settings, "SMS_PROVIDER", "stub")},
            )
        return {"sent": len(phone_numbers), "channel": "sms", "recipients": phone_numbers}

    def send_consent_request(self, relationship_id, channels=None):
        channels = channels or ["email"]
        relationship = (
            GuardianRelationship.objects.select_related("guardian", "child", "child__school")
            .filter(id=relationship_id, is_deleted=False)
            .first()
        )
        if relationship is None:
            return {"status": "missing", "relationship_id": relationship_id}
        if relationship.consent_status != GuardianRelationship.ConsentStatus.PENDING:
            return {
                "status": "skipped",
                "relationship_id": relationship.id,
                "reason": f"consent_status_is_{relationship.consent_status}",
            }

        child = relationship.child
        guardian = relationship.guardian
        school_name = child.school.name if child.school else "Clear Code Reading"
        consent_url = self._absolute_url("guardian-relationship-detail", relationship.id)
        subject = f"Consent needed for {child.first_name}'s Clear Code Reading account"
        message = (
            f"{school_name} is requesting your consent for {child.first_name} to use Clear Code Reading. "
            f"Please review and respond here: {consent_url}"
        )

        results = {}
        if "email" in channels:
            results["email"] = self.send_email(subject, message, [guardian.email])
        if "sms" in channels:
            results["sms"] = self.send_sms(message, [guardian.phone_number])

        AuditLog.objects.create(
            actor=guardian,
            action="notification.consent_request.sent",
            entity_type="GuardianRelationship",
            entity_id=str(relationship.id),
            after={"channels": channels, "child_id": child.id},
            metadata=results,
        )
        return {"status": "sent", "relationship_id": relationship.id, "results": results}

    def notify_evaluators_human_review_needed(self, assessment_id):
        assessment = (
            Assessment.objects.select_related("child", "school", "skill")
            .filter(id=assessment_id, is_deleted=False)
            .first()
        )
        if assessment is None:
            return {"status": "missing", "assessment_id": assessment_id}

        memberships = SchoolMembership.objects.filter(
            school=assessment.school or assessment.child.school,
            is_deleted=False,
            role__in=[
                SchoolMembership.Role.OWNER,
                SchoolMembership.Role.ADMIN,
                SchoolMembership.Role.TEACHER,
                SchoolMembership.Role.SPECIALIST,
            ],
        ).select_related("user")
        evaluator_emails = [membership.user.email for membership in memberships]
        if not evaluator_emails:
            evaluator_emails = list(
                CustomUser.objects.filter(role__in=[CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN], is_active=True)
                .exclude(email="")
                .values_list("email", flat=True)
            )

        subject = f"Human review needed: {assessment.title}"
        message = (
            f"{assessment.child} has submitted {assessment.title}. "
            "The assessment is now in Human Review and needs evaluator review in Clear Code Reading."
        )
        result = self.send_email(subject, message, evaluator_emails)

        AuditLog.objects.create(
            action="notification.assessment_human_review.sent",
            entity_type="Assessment",
            entity_id=str(assessment.id),
            after={"status": assessment.status, "recipient_count": result.get("sent", 0)},
            metadata=result,
        )
        return {"status": "sent", "assessment_id": assessment.id, "result": result}

    def notify_assessment_review_completed(self, assessment_id):
        assessment = (
            Assessment.objects.select_related("child", "assigned_by", "skill")
            .filter(id=assessment_id, is_deleted=False)
            .first()
        )
        if assessment is None:
            return {"status": "missing", "assessment_id": assessment_id}

        relationships = GuardianRelationship.objects.filter(
            child=assessment.child,
            is_deleted=False,
            consent_status=GuardianRelationship.ConsentStatus.GRANTED,
        ).select_related("guardian")
        recipients = [relationship.guardian.email for relationship in relationships]
        subject = f"{assessment.child.first_name}'s assessment review is complete"
        message = (
            f"The Clear Code Reading review for {assessment.title} is complete. "
            "Log in to view progress details and next lesson recommendations."
        )
        result = self.send_email(subject, message, recipients)

        AuditLog.objects.create(
            actor=assessment.assigned_by,
            action="notification.assessment_review_completed.sent",
            entity_type="Assessment",
            entity_id=str(assessment.id),
            after={"child_id": assessment.child_id, "skill_id": assessment.skill_id, "status": assessment.status},
            metadata=result,
        )
        return {"status": "sent", "assessment_id": assessment.id, "result": result}

    def send_progress_report_to_parents(self, child_id):
        from apps.users.models import ChildProfile

        child = ChildProfile.objects.filter(id=child_id, is_deleted=False).first()
        if child is None:
            return {"status": "missing", "child_id": child_id}

        relationships = GuardianRelationship.objects.filter(
            child=child,
            is_deleted=False,
            consent_status=GuardianRelationship.ConsentStatus.GRANTED,
        ).select_related("guardian")
        recipients = [relationship.guardian.email for relationship in relationships]
        progress_records = Progress.objects.filter(child=child, is_deleted=False).select_related("skill")
        mastered = progress_records.filter(status=Progress.Status.MASTERED).count()
        developing = progress_records.filter(status__in=[Progress.Status.EMERGING, Progress.Status.DEVELOPING]).count()
        lines = [
            f"Progress report for {child.first_name}",
            "",
            f"Total tracked skills: {progress_records.count()}",
            f"Mastered skills: {mastered}",
            f"Developing skills: {developing}",
            "",
            "Recent skill status:",
        ]
        for record in progress_records.order_by("-updated_at")[:10]:
            lines.append(f"- {record.skill.name}: {record.get_status_display()}")

        result = self.send_email(
            subject=f"{child.first_name}'s Clear Code Reading progress report",
            message="\n".join(lines),
            recipients=recipients,
        )
        AuditLog.objects.create(
            action="notification.progress_report.sent",
            entity_type="ChildProfile",
            entity_id=str(child.id),
            after={"recipient_count": result.get("sent", 0), "mastered": mastered, "developing": developing},
            metadata=result,
        )
        return {"status": "sent", "child_id": child.id, "result": result}

    def send_progress_reports_for_school(self, school_id):
        from apps.users.models import ChildProfile

        child_ids = ChildProfile.objects.filter(school_id=school_id, is_deleted=False).values_list("id", flat=True)
        results = [self.send_progress_report_to_parents(child_id) for child_id in child_ids]
        return {"status": "sent", "school_id": school_id, "children": len(results), "results": results}

    def _absolute_url(self, route_name, object_id):
        base_url = getattr(settings, "PUBLIC_APP_URL", "").rstrip("/")
        if route_name == "guardian-relationship-detail":
            path = f"/api/v1/guardian-relationships/{object_id}/"
        else:
            path = reverse(route_name, args=[object_id])
        return f"{base_url}{path}" if base_url else path


def consent_granted_recently(child, consent_type):
    return ConsentLog.objects.filter(
        child=child,
        consent_type=consent_type,
        status=ConsentLog.Status.GRANTED,
        created_at__lte=timezone.now(),
    ).exists()

```

## `apps/notifications/tasks.py`
```python
from celery import shared_task


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_consent_request_notification(self, relationship_id, channels=None):
    from apps.notifications.services import NotificationService

    return NotificationService().send_consent_request(relationship_id, channels=channels)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def notify_evaluator_assessment_human_review(self, assessment_id):
    from apps.notifications.services import NotificationService

    return NotificationService().notify_evaluators_human_review_needed(assessment_id)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def notify_assessment_review_completed(self, assessment_id):
    from apps.notifications.services import NotificationService

    return NotificationService().notify_assessment_review_completed(assessment_id)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_progress_report_to_parents(self, child_id):
    from apps.notifications.services import NotificationService

    return NotificationService().send_progress_report_to_parents(child_id)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_school_progress_reports(self, school_id):
    from apps.notifications.services import NotificationService

    return NotificationService().send_progress_reports_for_school(school_id)

```

## `apps/notifications/signals.py`
```python
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

```

## `apps/assessments/tasks.py`
```python
from celery import shared_task


@shared_task
def notify_assessment_review_completed(assessment_id):
    from apps.notifications.services import NotificationService

    return NotificationService().notify_assessment_review_completed(assessment_id)

```
