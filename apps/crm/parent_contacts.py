"""Link a newly provisioned parent account to the staff CRM."""

from django.db import transaction

from apps.crm.models import Lead
from apps.users.models import CustomUser


@transaction.atomic
def ensure_parent_contact(user: CustomUser) -> None:
    if user.role != CustomUser.Role.GUARDIAN or user.is_superuser or user.is_deleted:
        return
    # Serialize account provisioning and first-child setup for the same parent.
    CustomUser.objects.select_for_update().get(pk=user.pk)
    if Lead.objects.filter(linked_user=user).exists():
        # A staff member may have deliberately archived the linked contact.
        return
    email = user.email.strip().lower()
    contact = (
        Lead.objects.select_for_update()
        .filter(contact_email__iexact=email, linked_user__isnull=True, is_deleted=False)
        .order_by("-created_at", "-pk")
        .first()
    )
    if contact is not None:
        contact.linked_user = user
        if not contact.contact_phone:
            contact.contact_phone = user.phone_number
        contact.save(update_fields=["linked_user", "contact_phone", "updated_at"])
        return
    Lead.objects.create(
        linked_user=user,
        contact_name=(user.get_full_name() or email)[:255],
        contact_email=email,
        contact_phone=user.phone_number,
        audience=Lead.PipelineCategory.FAMILY_ENROLLMENT,
        school_name="Family",
        source=Lead.Source.OTHER,
    )
