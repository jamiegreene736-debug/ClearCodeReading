import base64
import binascii
import re
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from email.utils import getaddresses, parseaddr
from pathlib import PurePath
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from apps.crm.models import CrmActivity
from apps.crm_email.google import Gmail, ProviderError
from apps.crm_email.models import Attachment, Conversation, Mailbox, Message
from apps.crm_email.security import EmailError, clean_html, encrypt, plain_text


def provider_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{1,100}", value):
        raise EmailError("Google returned an invalid message identifier.")
    return value


def headers(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        .replace("\r", "")
        .replace("\n", " ")
        for item in payload.get("headers", [])
        if isinstance(item, dict)
    }


def addresses(value: str) -> list[str]:
    return list(
        dict.fromkeys(
            address.lower() for _, address in getaddresses([value]) if "@" in address
        )
    )


def decode(value: str) -> bytes:
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, binascii.Error) as exc:
        raise EmailError("Google returned unreadable email content.") from exc


def parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    pending = [payload]
    result = []
    while pending:
        node = pending.pop()
        result.append(node)
        if len(result) > 500:
            raise EmailError("This email has too many parts to import safely.")
        pending.extend(node.get("parts", []))
    return result


def message_content(
    client: Gmail, item: dict[str, Any]
) -> tuple[str, str, list[tuple[str, bytes]]]:
    text, html = [], []
    files = []
    total = 0
    for part in parts(item.get("payload", {})):
        body = part.get("body", {})
        filename = PurePath(str(part.get("filename", "")).replace("\\", "/")).name[:255]
        filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)
        size = int(body.get("size", 0))
        if total + size > settings.CRM_EMAIL_ATTACHMENT_LIMIT:
            text.append(
                "[Content exceeds the CRM import limit. Open the original message in Gmail.]"
            )
            continue
        raw = body.get("data", "")
        attachment_id = body.get("attachmentId")
        if attachment_id:
            # attachmentId is opaque; quote before including it in the provider URL.
            from urllib.parse import quote

            raw = client.request(
                "GET",
                f"messages/{provider_id(item['id'])}/attachments/{quote(str(attachment_id), safe='')}",
            ).get("data", "")
        if not raw:
            continue
        content = decode(raw)
        total += len(content)
        if total > settings.CRM_EMAIL_ATTACHMENT_LIMIT:
            raise EmailError("Email content exceeds the CRM import limit.")
        if filename:
            files.append((filename, content))
        elif part.get("mimeType") == "text/html":
            html.append(content.decode("utf-8", errors="replace")[:100000])
        elif part.get("mimeType") == "text/plain":
            text.append(content.decode("utf-8", errors="replace")[:100000])
    safe_html = (
        clean_html("\n".join(html))
        if html
        else "<p>" + str(escape("\n".join(text))).replace("\n", "<br>") + "</p>"
    )
    return "\n".join(text) or plain_text(safe_html), safe_html, files


def ensure_follow_up(message: Message) -> None:
    if message.lead is not None and message.follow_up_days and not message.follow_up_id:
        task = CrmActivity.objects.create(
            lead=message.lead,
            activity_type=CrmActivity.ActivityType.TASK,
            subject=f"Follow up: {message.subject}"[:255],
            body="Follow up on the email sent from the CRM.",
            created_by=message.mailbox.user,
            assigned_to=message.mailbox.user,
            due_at=(message.sent_at or timezone.now())
            + timedelta(days=message.follow_up_days),
        )
        message.follow_up = task
        message.save(update_fields=["follow_up"])


def store_thread(
    client: Gmail, conversation: Conversation, data: dict[str, Any] | None = None
) -> None:
    data = data if data is not None else client.thread(conversation.gmail_thread_id)
    items = data.get("messages", [])
    if not isinstance(items, list) or not items or len(items) > 500:
        raise EmailError("This conversation could not be imported. Open it in Gmail.")
    items = [item for item in items if "DRAFT" not in item.get("labelIds", [])]
    if not items:
        raise EmailError(
            "There are no sent or received messages to import in this thread."
        )
    if conversation.import_pending:
        participants = set()
        for item in items:
            head = headers(item.get("payload", {}))
            for field in ["from", "to", "cc"]:
                participants.update(addresses(head.get(field, "")))
        if conversation.lead.contact_email.lower() not in participants:
            raise EmailError("The selected thread does not include this contact.")
    # Prepare first so attachment failures cannot publish a partial historical import.
    prepared = []
    for item in items:
        gmail_id = provider_id(item.get("id"))
        if Message.objects.filter(
            mailbox=conversation.mailbox,
            gmail_id=gmail_id,
            status__in=[Message.Status.SENT, Message.Status.RECEIVED],
        ).exists():
            continue
        prepared.append((item, message_content(client, item)))
    with transaction.atomic():
        for item, (text, html, files) in prepared:
            head = headers(item.get("payload", {}))
            rfc_id = head.get("message-id", "")[:998]
            message = (
                Message.objects.filter(
                    mailbox=conversation.mailbox,
                    rfc_message_id=rfc_id,
                    status__in=[Message.Status.SENDING, Message.Status.UNCERTAIN],
                ).first()
                if rfc_id
                else None
            )
            if message and message.lead_id != conversation.lead_id:
                raise EmailError("This thread is linked to a different CRM contact.")
            message = message or Message(
                mailbox=conversation.mailbox, lead=conversation.lead
            )
            message.conversation = conversation
            message.gmail_id = provider_id(item["id"])
            message.rfc_message_id = rfc_id
            message.sender = parseaddr(head.get("from", ""))[1][:254].lower()
            message.reply_to = parseaddr(head.get("reply-to", ""))[1][:254].lower()
            message.to = addresses(head.get("to", ""))
            message.cc = addresses(head.get("cc", ""))
            message.subject = head.get("subject", "(No subject)")[:998]
            message.references = head.get("references", "")[:4000]
            message.in_reply_to = head.get("in-reply-to", "")[:998]
            message.body_text, message.body_html = text, html
            is_sent = "SENT" in item.get("labelIds", [])
            message.status = Message.Status.SENT if is_sent else Message.Status.RECEIVED
            try:
                message.sent_at = datetime.fromtimestamp(
                    int(item.get("internalDate", "0")) / 1000, tz=dt_timezone.utc
                )
            except (ValueError, OverflowError, OSError) as exc:
                raise EmailError("Google returned an invalid message date.") from exc
            message.last_error = ""
            message.save()
            message.attachments.all().delete()
            for filename, content in files:
                Attachment.objects.create(
                    message=message,
                    filename=filename,
                    size=len(content),
                    encrypted_data=encrypt(content),
                )
            if is_sent:
                ensure_follow_up(message)
        conversation.import_pending = False
        conversation.last_error = ""
        if items:
            conversation.subject = headers(items[0].get("payload", {})).get(
                "subject", "(No subject)"
            )[:998]
        conversation.save()


def sync_history(client: Gmail, mailbox: Mailbox) -> None:
    if not mailbox.history_id:
        mailbox.history_id = str(client.request("GET", "profile")["historyId"])
        return
    token = ""
    thread_ids: set[str] = set()
    latest = mailbox.history_id
    for _ in range(10):
        try:
            page = client.request(
                "GET",
                "history",
                params={
                    "startHistoryId": mailbox.history_id,
                    "historyTypes": "messageAdded",
                    "maxResults": 100,
                    **({"pageToken": token} if token else {}),
                },
            )
        except ProviderError as exc:
            if exc.status != 404:
                raise
            # A round-robin recovery refreshes every linked thread after an expired history cursor.
            mailbox.history_id = str(client.request("GET", "profile")["historyId"])
            mailbox.recovery_cursor = 0
            return
        for change in page.get("history", []):
            for added in change.get("messagesAdded", []):
                thread_ids.add(provider_id(added.get("message", {}).get("threadId")))
        latest = str(page.get("historyId", latest))
        token = str(page.get("nextPageToken", ""))
        if not token:
            break
    if token:
        # Do not silently advance past unprocessed changes. Recovery handles heavily active mailboxes.
        mailbox.history_id = str(client.request("GET", "profile")["historyId"])
        mailbox.recovery_cursor = 0
        return
    for conversation in mailbox.conversations.filter(
        gmail_thread_id__in=thread_ids, lead__is_deleted=False, import_pending=False
    ):
        store_thread(client, conversation)
    mailbox.history_id = latest


def synchronize(client: Gmail, mailbox: Mailbox) -> None:
    requested_at = mailbox.sync_requested_at
    sync_history(client, mailbox)
    pending = list(
        mailbox.conversations.filter(
            import_pending=True, lead__is_deleted=False
        ).order_by("pk")[:5]
    )
    recovery = list(
        mailbox.conversations.filter(
            import_pending=False, lead__is_deleted=False, pk__gt=mailbox.recovery_cursor
        ).order_by("pk")[:10]
    )
    for conversation in pending + recovery:
        try:
            store_thread(client, conversation)
        except EmailError as exc:
            conversation.last_error = str(exc)
            if isinstance(exc, ProviderError) and exc.retryable:
                raise
            conversation.save(update_fields=["last_error"])
    mailbox.recovery_cursor = recovery[-1].pk if len(recovery) == 10 else 0
    now = timezone.now()
    if not mailbox.watch_renewed_at or mailbox.watch_renewed_at < now - timedelta(
        hours=24
    ):
        watch = client.request(
            "POST", "watch", json={"topicName": settings.CRM_EMAIL_PUBSUB_TOPIC}
        )
        try:
            mailbox.watch_expires_at = datetime.fromtimestamp(
                int(watch["expiration"]) / 1000, tz=dt_timezone.utc
            )
        except (KeyError, ValueError, OverflowError, OSError) as exc:
            raise EmailError(
                "Google did not confirm the mailbox watch expiration."
            ) from exc
        mailbox.watch_renewed_at = now
    Mailbox.objects.filter(pk=mailbox.pk).update(
        history_id=mailbox.history_id,
        recovery_cursor=mailbox.recovery_cursor,
        watch_expires_at=mailbox.watch_expires_at,
        watch_renewed_at=mailbox.watch_renewed_at,
        last_sync_at=now,
        failures=0,
        next_attempt_at=None,
        last_error="",
    )
    # A push arriving during synchronization must survive this completion update.
    Mailbox.objects.filter(pk=mailbox.pk, sync_requested_at=requested_at).update(
        sync_requested_at=None
    )
