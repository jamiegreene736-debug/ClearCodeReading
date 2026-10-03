from celery import shared_task
from django_tenants.utils import schema_context

from apps.social.planner import maintain_content_plan


@shared_task
def publish_scheduled_social_posts():
    """Publish social posts whose Eastern time has arrived. Runs from the public schema."""
    from apps.social.services import publish_due

    with schema_context("public"):
        count = publish_due()
        from apps.blog.planner import maintain_blog_plan
        try:
            maintain_content_plan()
        finally:
            maintain_blog_plan()
        return count
