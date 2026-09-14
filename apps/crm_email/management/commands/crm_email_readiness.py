import json
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

from apps.crm_email.models import Mailbox, Message
from apps.crm_email.security import configuration_errors
from apps.crm_email.services import worker_healthy


class Command(BaseCommand):
    help = "Report CRM email readiness without exposing credentials or email content."

    def handle(self, *args: Any, **options: Any) -> None:
        with schema_context("public"):
            errors = configuration_errors()
            self.stdout.write(
                json.dumps(
                    {
                        "enabled": settings.CRM_EMAIL_ENABLED,
                        "configuration_ready": not errors,
                        "configuration_errors": errors,
                        "worker_healthy": worker_healthy(),
                        "connected_mailboxes": Mailbox.objects.filter(
                            status=Mailbox.Status.CONNECTED
                        ).count(),
                        "reconnect_required": Mailbox.objects.filter(
                            status=Mailbox.Status.RECONNECT
                        ).count(),
                        "queued_messages": Message.objects.filter(
                            status=Message.Status.QUEUED
                        ).count(),
                        "uncertain_messages": Message.objects.filter(
                            status__in=[
                                Message.Status.UNCERTAIN,
                                Message.Status.SENDING,
                            ]
                        ).count(),
                    }
                )
            )
