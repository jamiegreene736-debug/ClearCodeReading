"""Restore a contact by stable ID without changing related records."""

from django.core.management.base import BaseCommand, CommandError

from apps.crm.contact_lifecycle import set_contact_deleted
from apps.crm.models import Lead
from apps.users.models import CustomUser


class Command(BaseCommand):
    help = "Restore a soft-deleted CRM contact, audited to an active administrator."

    def add_arguments(self, parser):
        parser.add_argument("contact_id", type=int)
        parser.add_argument("--actor-id", type=int, required=True)

    def handle(self, *args, **options):
        actor = CustomUser.objects.filter(
            pk=options["actor_id"], is_active=True, is_deleted=False
        ).first()
        if actor is None or not actor.can_manage_crm_users:
            raise CommandError("Choose an active CRM administrator as the actor.")
        try:
            contact = set_contact_deleted(
                contact_id=options["contact_id"], actor=actor, deleted=False
            )
        except Lead.DoesNotExist as exc:
            raise CommandError("CRM contact ID does not exist.") from exc
        self.stdout.write(self.style.SUCCESS(f"CRM contact {contact.pk} is active."))
