from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

from apps.social.services import publish_due


class Command(BaseCommand):
    help = "Publish social posts whose scheduled Eastern time has arrived."

    def handle(self, *args, **options):
        with schema_context("public"):
            count = publish_due()
        self.stdout.write(f"Claimed {count} scheduled social post(s).")
