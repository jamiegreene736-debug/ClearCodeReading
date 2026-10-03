import logging

from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

from apps.social.planner import maintain_content_plan
from apps.social.services import publish_due


class Command(BaseCommand):
    help = "Publish social posts whose scheduled Eastern time has arrived."

    def handle(self, *args, **options):
        with schema_context("public"):
            count = publish_due()
            from apps.blog.planner import maintain_blog_plan
            failures = []
            for name, maintain in (("social", maintain_content_plan), ("blog", maintain_blog_plan)):
                try:
                    generated = maintain()
                    self.stdout.write(f"Prepared {generated} weekly {name} post(s).")
                except Exception as exc:
                    # A failing planner must not starve the other or stop due deliveries.
                    logging.getLogger(__name__).exception("%s_planner_failed", name)
                    failures.append(exc)
            if failures:
                raise failures[0]
        self.stdout.write(f"Claimed {count} scheduled social post(s).")
