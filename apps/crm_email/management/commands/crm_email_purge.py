from datetime import datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.crm_email.models import Conversation, Mailbox, Message
from apps.crm_email.security import mailbox_lock
from apps.users.models import AuditLog


class Command(BaseCommand):
    help = "Purge email for one CRM contact before a cutoff. Dry run unless --execute is provided."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--lead-id", type=int, required=True)
        parser.add_argument(
            "--before", required=True, help="ISO 8601 date/time with timezone"
        )
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            cutoff = datetime.fromisoformat(options["before"])
        except ValueError as exc:
            raise CommandError("Provide an ISO 8601 date/time with timezone.") from exc
        if timezone.is_naive(cutoff):
            raise CommandError("Cutoff must include a timezone.")
        with schema_context("public"):
            # Delete entire old threads so background synchronization cannot silently recreate purged messages.
            conversations = (
                Conversation.objects.filter(
                    lead_id=options["lead_id"], created_at__lt=cutoff
                )
                .exclude(messages__created_at__gte=cutoff)
                .exclude(messages__status__in=["queued", "sending", "uncertain"])
            )
            standalone = Message.objects.filter(
                lead_id=options["lead_id"],
                conversation__isnull=True,
                created_at__lt=cutoff,
            ).exclude(status__in=["queued", "sending", "uncertain"])
            self.stdout.write(
                f"Eligible conversations: {conversations.count()}; standalone messages: {standalone.count()}"
            )
            if not options["execute"]:
                self.stdout.write("Dry run. No records changed.")
                return
            for mailbox in Mailbox.objects.filter(user__isnull=False).iterator():
                with mailbox_lock(mailbox.pk), transaction.atomic():
                    conversations.filter(mailbox=mailbox).delete()
                    standalone.filter(mailbox=mailbox).delete()
            AuditLog.objects.create(
                action="crm.email.purged",
                entity_type="Lead",
                entity_id=str(options["lead_id"]),
                metadata={"before": cutoff.isoformat(), "source": "management_command"},
            )
            self.stdout.write(
                "Eligible email history and encrypted attachments purged. Backup retention is managed separately."
            )
