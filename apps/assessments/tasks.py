from celery import shared_task


@shared_task
def notify_assessment_review_completed(assessment_id):
    from apps.notifications.services import NotificationService

    return NotificationService().notify_assessment_review_completed(assessment_id)
