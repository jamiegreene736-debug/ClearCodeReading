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
