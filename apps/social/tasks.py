from celery import shared_task
from django_tenants.utils import schema_context


@shared_task
def publish_scheduled_social_posts():
    """Publish social posts whose Eastern time has arrived. Runs from the public schema."""
    from apps.social.services import publish_due

    with schema_context("public"):
        return publish_due()
