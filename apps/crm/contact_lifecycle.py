"""Recoverable contact removal; related records remain intact for restoration."""

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.crm.models import Lead
from apps.users.models import AuditLog, CustomUser


def set_contact_deleted(*, contact_id: int, actor: CustomUser, deleted: bool) -> Lead:
    if not (actor.is_active and not actor.is_deleted and actor.can_manage_crm_users):
        raise PermissionDenied(
            "Only administrators can delete or restore CRM contacts."
        )
    with transaction.atomic():
        contact = Lead.objects.select_for_update().get(pk=contact_id)
        if contact.is_deleted == deleted:
            return contact
        before = {"is_deleted": contact.is_deleted}
        contact.is_deleted = deleted
        contact.deleted_at = timezone.now() if deleted else None
        contact.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        AuditLog.objects.create(
            actor=actor,
            action="crm.contact.deleted" if deleted else "crm.contact.restored",
            entity_type="Lead",
            entity_id=str(contact.pk),
            before=before,
            after={
                "is_deleted": deleted,
                "deleted_at": contact.deleted_at.isoformat()
                if contact.deleted_at
                else None,
            },
        )
    return contact
