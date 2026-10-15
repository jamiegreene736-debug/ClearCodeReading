"""Standalone alternative to the hourly Celery analytics task."""
from typing import Any

from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

from apps.social.insights import sync_due_insights


class Command(BaseCommand):
    help = "Collect due Facebook and Instagram post metrics (safe to run hourly)."

    def handle(self, *args: Any, **options: Any) -> None:
        with schema_context("public"):
            count = sync_due_insights()
        self.stdout.write(f"Updated {count} publication(s).")
