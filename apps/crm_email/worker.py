import base64
import logging
import random
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.crm_email.google import Gmail, ProviderError
from apps.crm_email.models import (
    Authorization,
    Conversation,
    Mailbox,
    Message,
    WorkerHeartbeat,
)
from apps.crm_email.security import (
    EmailError,
    mailbox_lock,
    require_configured,
    require_crm,
)
from apps.crm_email.services import build_mime
from apps.crm_email.sync import ensure_follow_up, provider_id, synchronize
from apps.users.models import AuditLog

logger = logging.getLogger(__name__)


def backoff(attempts: int) -> timedelta:
    return timedelta(
        seconds=min(3600, 30 * 2 ** min(attempts, 7)) + random.uniform(0, 15)
    )


def accepted(message: Message, gmail_id: str, thread_id: str) -> None:
    with transaction.atomic():
        if message.lead is not None:
            conversation, _ = Conversation.objects.get_or_create(
                mailbox=message.mailbox,
                gmail_thread_id=thread_id,
                defaults={"lead": message.lead, "subject": message.subject},
            )
            if conversation.lead_id != message.lead_id:
                raise EmailError(
                    "Google attached this message to a thread linked to another contact. Review in Gmail."
                )
            message.conversation = conversation
        message.gmail_id = gmail_id
        message.sent_at = timezone.now()
        message.status = Message.Status.SENT
        message.last_error = ""
        message.save()
        if message.lead is not None:
            ensure_follow_up(message)
        AuditLog.objects.create(
            actor=message.mailbox.user,
            action="crm.email.sent",
            entity_type="Message",
            entity_id=str(message.pk),
        )


def reconcile(client: Gmail, message: Message) -> None:
    result = client.request(
        "GET",
        "messages",
        params={"q": f"in:sent rfc822msgid:{message.rfc_message_id}", "maxResults": 2},
    )
    matches = result.get("messages", [])
    if len(matches) == 1:
        accepted(
            message,
            provider_id(matches[0].get("id")),
            provider_id(matches[0].get("threadId")),
        )
        return
    message.status = Message.Status.UNCERTAIN
    message.attempts += 1
    message.next_attempt_at = timezone.now() + backoff(message.attempts)
    message.last_error = "Send result is uncertain. Check Sent in Gmail; this message will not be resent automatically."
    message.save()


def send(client: Gmail, message: Message) -> None:
    if message.status not in {
        Message.Status.QUEUED,
        Message.Status.SENDING,
        Message.Status.UNCERTAIN,
    }:
        return
    if message.status in {Message.Status.SENDING, Message.Status.UNCERTAIN}:
        reconcile(client, message)
        return
    mailbox = Mailbox.objects.select_related("user").get(pk=message.mailbox_id)
    if (
        mailbox.status != Mailbox.Status.CONNECTED
        or not mailbox.user.has_crm_access
        or not mailbox.user.is_active
        or mailbox.user.is_deleted
        or mailbox.email.lower() != mailbox.user.email.lower()
        or message.sender.lower() != mailbox.email.lower()
    ):
        message.status = Message.Status.FAILED
        message.last_error = "Sender no longer matches an active connected Gmail account. Reconnect your Gmail and create a new message."
        message.save()
        return
    from apps.crm.inventory_models import InventoryMail

    delivery = (
        InventoryMail.objects.filter(provider_message=message)
        .select_related("invitation")
        .first()
    )
    if (
        delivery
        and "/reading-inventory/" in delivery.action_url
        and (
            delivery.invitation.revoked_at
            or delivery.invitation.expires_at <= timezone.now()
        )
    ):
        message.status = Message.Status.CANCELLED
        message.last_error = "Assessment link expired or was withdrawn before delivery."
        message.save()
        return
    if message.lead is not None and message.lead.is_deleted:
        message.status = Message.Status.CANCELLED
        message.last_error = "Contact was deleted before sending."
        message.save()
        return
    try:
        raw = build_mime(message)
    except (EmailError, ValueError):
        message.status = Message.Status.FAILED
        message.last_error = "The draft could not be prepared safely. Review its content and attachments."
        message.save()
        return
    message.status = Message.Status.SENDING
    message.started_at = timezone.now()
    message.attempts += 1
    message.save()
    payload = {"raw": base64.urlsafe_b64encode(raw).decode()}
    if message.conversation is not None:
        payload["threadId"] = message.conversation.gmail_thread_id
    try:
        result = client.request("POST", "messages/send", json=payload)
        accepted(
            message, provider_id(result.get("id")), provider_id(result.get("threadId"))
        )
    except ProviderError as exc:
        if exc.status in {429, 403} and exc.retryable and message.attempts < 6:
            message.status = Message.Status.QUEUED
        elif exc.status and exc.status < 500:
            message.status = Message.Status.FAILED
        else:
            message.status = Message.Status.UNCERTAIN
        message.last_error = (
            "Google rejected this send. Review it before creating a new draft."
            if message.status == Message.Status.FAILED
            else "Google has not confirmed this send yet."
        )
        message.next_attempt_at = timezone.now() + backoff(message.attempts)
        message.save()
        if message.status == Message.Status.QUEUED:
            raise  # Back off the whole mailbox when Gmail is rate-limiting it.
    except EmailError:
        message.status = Message.Status.UNCERTAIN
        message.last_error = (
            "Send result needs reconciliation before any further action."
        )
        message.next_attempt_at = timezone.now() + timedelta(minutes=1)
        message.save()


def heartbeat() -> None:
    WorkerHeartbeat.objects.update_or_create(
        name="email", defaults={"last_seen_at": timezone.now()}
    )


def run_pass() -> int:
    if getattr(connection, "schema_name", "") != "public":
        raise EmailError("CRM email worker must run in the public schema.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [73490, 1])
        acquired = cursor.fetchone()[0]
    if not acquired:
        return 0
    processed = 0
    try:
        heartbeat()
        Authorization.objects.filter(expires_at__lt=timezone.now()).delete()
        if not settings.CRM_EMAIL_ENABLED:
            return 0
        require_configured()
        from apps.crm.website_emails import enqueue_pending_receipts

        enqueue_pending_receipts()
        # Recover invitations committed just before a web request was interrupted.
        from apps.crm.inventory_mail import enqueue_google
        from apps.crm.inventory_models import InventoryMail

        pending_inventory = InventoryMail.objects.filter(status="pending").values_list(
            "pk", flat=True
        )[:50]
        for delivery_id in pending_inventory:
            enqueue_google(delivery_id)
        mailboxes = (
            Mailbox.objects.filter(status=Mailbox.Status.CONNECTED)
            .select_related("user")
            .order_by("last_sync_at", "pk")
        )
        for mailbox in mailboxes.iterator():
            heartbeat()
            with mailbox_lock(mailbox.pk, wait=True):
                mailbox.refresh_from_db()
                if mailbox.status != Mailbox.Status.CONNECTED:
                    continue
                try:
                    # Refresh the user's permissions rather than trusting a potentially stale worker object.
                    mailbox.user.refresh_from_db()
                    require_crm(mailbox.user)
                    if mailbox.email.lower() != mailbox.user.email.lower():
                        raise PermissionDenied
                except PermissionDenied:
                    # No credentials or queued sends survive loss of CRM access.
                    from apps.crm_email.google import revoke

                    revoke(mailbox)
                    mailbox.encrypted_refresh_token = ""
                    mailbox.status = Mailbox.Status.DISCONNECTED
                    mailbox.last_error = "CRM access removed. Mailbox disconnected."
                    mailbox.save()
                    mailbox.messages.filter(status=Message.Status.QUEUED).update(
                        status=Message.Status.CANCELLED
                    )
                    continue
                if mailbox.next_attempt_at and mailbox.next_attempt_at > timezone.now():
                    continue
                client = Gmail(mailbox)
                try:
                    pending = (
                        mailbox.messages.filter(
                            status__in=[
                                Message.Status.QUEUED,
                                Message.Status.SENDING,
                                Message.Status.UNCERTAIN,
                            ]
                        )
                        .filter(
                            Q(scheduled_at__isnull=True)
                            | Q(scheduled_at__lte=timezone.now())
                        )
                        .filter(
                            Q(next_attempt_at__isnull=True)
                            | Q(next_attempt_at__lte=timezone.now())
                        )
                        .select_related("lead", "conversation")
                        .order_by("created_at")[:5]
                    )
                    for message in pending:
                        if not Mailbox.objects.filter(
                            pk=mailbox.pk, status=Mailbox.Status.CONNECTED
                        ).exists():
                            break
                        send(client, message)
                        heartbeat()
                        processed += 1
                    if (
                        mailbox.sync_requested_at
                        or not mailbox.last_sync_at
                        or mailbox.last_sync_at < timezone.now() - timedelta(minutes=5)
                    ):
                        synchronize(client, mailbox)
                except EmailError as exc:
                    Mailbox.objects.filter(pk=mailbox.pk).update(
                        failures=mailbox.failures + 1,
                        next_attempt_at=timezone.now() + backoff(mailbox.failures + 1),
                        last_error=str(exc),
                    )
                    logger.warning(
                        "crm_email_provider_failure mailbox_id=%s error_type=%s",
                        mailbox.pk,
                        type(exc).__name__,
                    )
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    AttributeError,
                    OverflowError,
                ) as exc:
                    # Isolate malformed third-party data or unexpected per-account failures so one mailbox
                    # cannot starve the team. In-flight sends remain marked for reconciliation.
                    Mailbox.objects.filter(pk=mailbox.pk).update(
                        failures=mailbox.failures + 1,
                        next_attempt_at=timezone.now() + backoff(mailbox.failures + 1),
                        last_error="Email processing needs attention. The worker will retry safely.",
                    )
                    logger.error(
                        "crm_email_mailbox_failure mailbox_id=%s error_type=%s",
                        mailbox.pk,
                        type(exc).__name__,
                    )
        heartbeat()
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [73490, 1])
    return processed
