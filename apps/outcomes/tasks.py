from celery import shared_task

from apps.outcomes.services import aggregate_outcomes, parse_window


@shared_task
def aggregate_previous_quarter_outcomes():
    window = parse_window("quarter")
    return [snapshot.id for snapshot in aggregate_outcomes(window=window)]
