import re
from datetime import timedelta
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from pathlib import PurePath
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.crm.models import Lead
from apps.crm_email.forms import ComposeForm
from apps.crm_email.google import Gmail, revoke
from apps.crm_email.models import (
    Attachment,
    Mailbox,
    Message,
    WorkerHeartbeat,
)
from apps.crm_email.security import (
    EmailError,
    decrypt,
    encrypt,
    mailbox_lock,
    plain_text,
    require_configured,
    require_crm,
)
from apps.users.models import AuditLog, CustomUser

FINAL_STATUSES = [Message.Status.SENT, Message.Status.RECEIVED]


def worker_healthy() -> bool:
    return WorkerHeartbeat.objects.filter(
        name="email", last_seen_at__gte=timezone.now() - timedelta(minutes=3)
    ).exists()


def active_mailbox(user: CustomUser) -> Mailbox:
    require_crm(user)
    mailbox, _ = Mailbox.objects.get_or_create(user=user)
    if (
        mailbox.status != Mailbox.Status.CONNECTED
        or mailbox.email.lower() != user.email.lower()
    ):
        raise EmailError(
            "Connect your Google mailbox before sending or importing email."
        )
    return mailbox


def save_message(
    user: CustomUser, lead: Lead, form: ComposeForm, uploads: list[Any], action: str
) -> Message:
    require_crm(user)
    mailbox, _ = Mailbox.objects.get_or_create(user=user)
    with mailbox_lock(mailbox.pk), transaction.atomic():
        mailbox.refresh_from_db()
        if lead.is_deleted:
            raise PermissionDenied
        data = form.cleaned_data
        if lead.contact_email.lower() not in data["to"] + data["cc"]:
            raise EmailError(
                "Include this contact in To or CC so the conversation belongs on this record."
            )
        message = (
            Message.objects.select_for_update().filter(pk=data["draft_id"]).first()
        )
        if message:
            if message.mailbox_id != mailbox.pk or message.lead_id != lead.pk:
                raise PermissionDenied
            if message.status != Message.Status.DRAFT:
                # An already-submitted form must never send a second copy.
                return message
        else:
            message = Message(id=data["draft_id"], mailbox=mailbox, lead=lead)
        if action == "send":
            require_configured()
            active_mailbox(user)
            if not worker_healthy():
                raise EmailError(
                    "The email worker is not ready. Save a draft and try again shortly."
                )
        reply = None
        if data.get("reply_id"):
            reply = Message.objects.filter(
                pk=data["reply_id"],
                mailbox=mailbox,
                lead=lead,
                status__in=FINAL_STATUSES,
                conversation__isnull=False,
            ).first()
            if not reply:
                raise EmailError(
                    "Reply using a conversation from your own connected mailbox."
                )
            message.conversation = reply.conversation
            message.in_reply_to = reply.rfc_message_id
            message.references = (
                reply.references + " " + reply.rfc_message_id
            ).strip()[-4000:]
            data["subject"] = reply.subject
        for name in [
            "to",
            "cc",
            "bcc",
            "subject",
            "body_html",
            "scheduled_at",
            "follow_up_days",
        ]:
            setattr(message, name, data[name])
        message.body_text = plain_text(message.body_html)
        message.sender = mailbox.email or user.email
        message.rfc_message_id = f"<crm-{message.pk}@{settings.CRM_EMAIL_DOMAIN}>"
        message.status = (
            Message.Status.QUEUED if action == "send" else Message.Status.DRAFT
        )
        existing_size = (
            sum(message.attachments.values_list("size", flat=True))
            if message.pk and not message._state.adding
            else 0
        )
        if (
            existing_size + sum(upload.size for upload in uploads)
            > settings.CRM_EMAIL_ATTACHMENT_LIMIT
        ):
            raise EmailError("Attachments must total 10 MB or less.")
        message.save()
        for upload in uploads:
            filename = PurePath(upload.name.replace("\\", "/")).name[:255]
            if not filename or re.search(r"[\x00-\x1f\x7f]", filename):
                raise EmailError("Choose an attachment with a valid filename.")
            Attachment.objects.create(
                message=message,
                filename=filename,
                size=upload.size,
                encrypted_data=encrypt(upload.read()),
            )
        AuditLog.objects.create(
            actor=user,
            action=f"crm.email.{message.status}",
            entity_type="Message",
            entity_id=str(message.pk),
        )
        return message


def build_mime(message: Message) -> bytes:
    mail = EmailMessage(policy=SMTP)
    for name, value in [
        ("From", message.sender),
        ("To", ", ".join(message.to)),
        ("Cc", ", ".join(message.cc)),
        ("Bcc", ", ".join(message.bcc)),
        ("Subject", message.subject),
        ("Message-ID", message.rfc_message_id),
        ("Reply-To", message.reply_to),
        ("In-Reply-To", message.in_reply_to),
        ("References", message.references),
    ]:
        if value:
            mail[name] = value
    mail["Date"] = format_datetime(timezone.now())
    mail.set_content(message.body_text)
    mail.add_alternative(message.body_html, subtype="html")
    for attachment in message.attachments.all():
        mail.add_attachment(
            decrypt(bytes(attachment.encrypted_data)),
            maintype="text" if attachment.filename.endswith(".ics") else "application",
            subtype="calendar"
            if attachment.filename.endswith(".ics")
            else "octet-stream",
            params={"method": "REQUEST"}
            if attachment.filename.endswith(".ics")
            else None,
            filename=attachment.filename,
        )
    return mail.as_bytes()


def disconnect(mailbox: Mailbox, actor: CustomUser) -> bool:
    require_crm(actor)
    if mailbox.user_id != actor.pk and not actor.can_manage_crm_users:
        raise PermissionDenied
    with mailbox_lock(mailbox.pk):
        mailbox.refresh_from_db()
        if mailbox.status == Mailbox.Status.CONNECTED:
            try:
                Gmail(mailbox).request("POST", "stop")
            except EmailError:
                pass  # Local disconnection must succeed even if Google is unavailable.
        revoked = revoke(mailbox)
        mailbox.encrypted_refresh_token = ""
        mailbox.status = Mailbox.Status.DISCONNECTED
        mailbox.watch_expires_at = None
        mailbox.watch_renewed_at = None
        mailbox.sync_requested_at = None
        mailbox.last_error = (
            ""
            if revoked
            else "Disconnected locally. Remove CRM access in your Google account to finish revocation."
        )
        mailbox.save()
        mailbox.messages.filter(status=Message.Status.QUEUED).update(
            status=Message.Status.CANCELLED,
            last_error="Mailbox disconnected before sending.",
        )
        mailbox.conversations.filter(import_pending=True).delete()
        AuditLog.objects.create(
            actor=actor,
            action="crm.email.disconnected",
            entity_type="Mailbox",
            entity_id=str(mailbox.pk),
        )
        return revoked
