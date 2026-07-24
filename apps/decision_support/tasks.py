from celery import shared_task
from django.db import OperationalError


@shared_task(
    bind=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def evaluate_completed_session(self, session_id):
    from apps.decision_support.interfaces import get_decision_support_engine
    from apps.sessions.models import Session

    session = Session.objects.filter(pk=session_id, status=Session.Status.COMPLETED, is_deleted=False).first()
    if session is None:
        return {"status": "missing", "session_id": session_id}
    engine = get_decision_support_engine()
    flags = engine.evaluate_completed_session(session.id)
    try:
        prediction = engine.generate_milestone_prediction(session.child_id)
        prediction_id = prediction.id
        prediction_status = "generated"
    except ValueError:
        prediction_id = None
        prediction_status = "placement_required"
    return {
        "status": "evaluated",
        "session_id": session.id,
        "flag_ids": [flag.id for flag in flags],
        "prediction_id": prediction_id,
        "prediction_status": prediction_status,
    }


@shared_task(
    bind=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def notify_high_growth_flag(self, flag_id):
    from apps.notifications.services import NotificationService

    return NotificationService().notify_growth_flag_opened(flag_id)
