"""Bridge assessment messages into the CRM's connected-mailbox outbox."""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.template.loader import render_to_string
from django.utils import timezone

from apps.crm.inventory_models import InventoryInvitation, InventoryMail
from apps.crm_email.models import Attachment, Mailbox, Message
from apps.crm_email.security import (
    EmailError,
    encrypt,
    mailbox_lock,
    require_configured,
)
from apps.crm_email.services import active_mailbox


def enqueue_google(pk: int) -> None:
    delivery = InventoryMail.objects.select_related("invitation__created_by").get(pk=pk)
    if delivery.status != "pending":
        return
    try:
        require_configured()
        if not delivery.invitation.created_by:
            raise EmailError(
                "The original sender is unavailable. Ask a CRM user to send a new invitation."
            )
        mailbox = active_mailbox(delivery.invitation.created_by)
        # Match the email worker's lock order before touching either outbox row.
        with mailbox_lock(mailbox.pk), transaction.atomic():
            delivery = (
                InventoryMail.objects.select_for_update(of=("self",))
                .select_related("invitation__child__parent")
                .get(pk=pk)
            )
            if delivery.status != "pending":
                return
            _enqueue_locked(delivery, mailbox)
    except (EmailError, PermissionDenied) as exc:
        error = (
            str(exc)[:200]
            if isinstance(exc, EmailError)
            else "The original sender no longer has CRM email access."
        )
        InventoryMail.objects.filter(pk=pk, status="pending").update(
            status="failed", error=error
        )


def _enqueue_locked(delivery: InventoryMail, mailbox: Mailbox) -> None:
    invitation = delivery.invitation
    if not settings.PUBLIC_APP_URL.startswith("https://"):
        raise EmailError(
            "Set PUBLIC_APP_URL to the public HTTPS website before sending assessment links."
        )
    if "/reading-inventory/" in delivery.action_url and (
        invitation.revoked_at or invitation.expires_at <= timezone.now()
    ):
        raise EmailError(
            "The assessment link expired or was withdrawn. Send a new invitation."
        )
    if delivery.provider_message_id:
        message = Message.objects.select_for_update().get(
            pk=delivery.provider_message_id
        )
        if message.status == Message.Status.FAILED:
            message.status = Message.Status.QUEUED
            message.last_error = ""
            message.next_attempt_at = None
            message.save()
        else:
            sync_delivery(Message, message)
        return
    encrypted_calendar = (
        encrypt(delivery.calendar.encode()) if delivery.calendar else None
    )
    message = Message.objects.create(
        mailbox=mailbox,
        lead=invitation.child.parent,
        sender=mailbox.email,
        to=[delivery.recipient],
        subject=delivery.subject,
        body_text=delivery.body
        + (
            f"\n\n{delivery.action_label}: {delivery.action_url}"
            if delivery.action_url
            else ""
        ),
        body_html=render_to_string("crm/inventory_email.html", {"email": delivery}),
        status=Message.Status.DRAFT,
    )
    message.rfc_message_id = f"<crm-{message.pk}@{settings.CRM_EMAIL_DOMAIN}>"
    if delivery.calendar:
        payload = delivery.calendar.encode()
        Attachment.objects.create(
            message=message,
            filename="consultation.ics",
            size=len(payload),
            encrypted_data=encrypted_calendar,
        )
    delivery.provider_message = message
    delivery.status = InventoryMail.Status.QUEUED
    delivery.error = ""
    delivery.save()
    message.status = Message.Status.QUEUED
    message.save()


@receiver(post_save, sender=Message, dispatch_uid="inventory_email_delivery_status")
def sync_delivery(sender: type[Message], instance: Message, **kwargs: object) -> None:
    status = {
        "draft": "pending",
        "queued": "queued",
        "sending": "sending",
        "uncertain": "sending",
        "sent": "sent",
        "failed": "failed",
        "cancelled": "failed",
    }.get(instance.status)
    if not status:
        return
    deliveries = InventoryMail.objects.filter(provider_message=instance)
    deliveries.update(
        status=status,
        error=instance.last_error[:200],
        attempts=instance.attempts,
        attempted_at=instance.started_at,
        sent_at=instance.sent_at,
    )
    if instance.status == Message.Status.SENT:
        ids = deliveries.filter(key__startswith="inventory_send_").values_list(
            "invitation_id", flat=True
        )
        InventoryInvitation.objects.filter(pk__in=ids, sent_at__isnull=True).update(
            sent_at=instance.sent_at
        )
