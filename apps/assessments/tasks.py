from celery import shared_task


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def compute_assessment_results_after_survey_completion(self, assessment_id):
    from apps.assessments.models import Assessment
    from apps.assessments.services import compute_and_persist_assessment_result
    from apps.notifications.tasks import notify_evaluator_assessment_human_review

    previous_status = Assessment.objects.filter(id=assessment_id).values_list("status", flat=True).first()
    result = compute_and_persist_assessment_result(assessment_id)
    notification_queued = previous_status in {Assessment.Status.PENDING, Assessment.Status.IN_PROGRESS}
    if previous_status == Assessment.Status.HUMAN_REVIEW:
        notify_evaluator_assessment_human_review.delay(assessment_id)
        notification_queued = True

    return {
        "status": "computed",
        "assessment_id": assessment_id,
        "result_id": result.id,
        "reading_age": float(result.reading_age),
        "overall_score": result.final_scores.get("overall_score"),
        "final_message": result.final_scores.get("final_message"),
        "notification_queued": notification_queued,
    }


@shared_task
def notify_assessment_review_completed(assessment_id):
    from apps.notifications.services import NotificationService

    return NotificationService().notify_assessment_review_completed(assessment_id)
