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
            try:
                generated = maintain_content_plan()
            except Exception:
                # Publishing must continue even if a planner dependency fails unexpectedly.
                logging.getLogger(__name__).exception("social_planner_failed")
                raise
            self.stdout.write(f"Prepared {generated} weekly content post(s).")
        self.stdout.write(f"Claimed {count} scheduled social post(s).")
