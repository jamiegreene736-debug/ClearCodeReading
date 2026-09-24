"""Versioned parent inventory scoring, invitations, and durable email delivery."""

import json
import logging
import smtplib
from datetime import timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.crm.inventory_email import plain_text
from apps.crm.inventory_models import InventoryInvitation, InventoryMail
from apps.crm.models import CrmActivity
from apps.crm.newsletters import newsletter_delivery_configuration_errors
from apps.crm_email.automated import copy_for as automated_copy
from apps.crm_email.automated import fill
from apps.users.models import AuditLog

logger = logging.getLogger(__name__)
DATA = json.loads((Path(__file__).parent / "data/parent_inventory_v1.json").read_text())
GRADES = {item["value"]: item for item in DATA["grades"]}
SALT = "crm.parent-inventory.v1"


class InventoryError(ValueError):
    pass


def definition(grade: str) -> dict[str, Any]:
    if grade not in GRADES:
        raise InventoryError("Choose a valid grade.")
    return DATA["inventories"][GRADES[grade]["inventoryKey"]]


def evaluate(grade: str, answers: dict[str, bool]) -> dict[str, Any]:
    spec = definition(grade)
    allowed = {q["id"] for g in spec["groups"] for q in g["questions"]}
    if (
        not isinstance(answers, dict)
        or set(answers) - allowed
        or any(type(v) is not bool for v in answers.values())
    ):
        raise InventoryError("Some answers are not valid for this grade.")
    visited: set[str] = set()
    score = 0
    support_rule = None
    for index, group in enumerate(spec["groups"]):
        ids = {q["id"] for q in group["questions"]}
        visited |= ids
        if not ids <= answers.keys():
            if set(answers) - visited:
                raise InventoryError("Complete the earlier section first.")
            return {"complete": False, "next_group": index}
        count = sum(answers[key] for key in ids)
        score += count
        if count < group.get("continueAt", 0) and support_rule is None:
            support_rule = f"section-{index + 1}-below-{group['continueAt']}"
    if support_rule:
        return {
            "complete": True,
            "outcome": "support",
            "rule": support_rule,
            "yes_count": score,
            "answered": len(answers),
            "total": len(allowed),
        }
    resource_at = spec["resourceAt"]
    # Source percentage/Yes-count wording conflicts at these exact totals.
    outcome = (
        "review"
        if score == resource_at - 1
        else ("resources" if score >= resource_at else "support")
    )
    return {
        "complete": True,
        "outcome": outcome,
        "rule": f"total-{outcome}-v1",
        "yes_count": score,
        "answered": len(answers),
        "total": len(allowed),
    }


def token_for(invitation: InventoryInvitation) -> str:
    return signing.dumps(str(invitation.pk), salt=SALT)


def invitation_url(invitation: InventoryInvitation) -> str:
    return settings.PUBLIC_APP_URL.rstrip("/") + reverse(
        "inventory_public", args=[token_for(invitation)]
    )


def resolve_token(token: str) -> InventoryInvitation:
    try:
        pk = signing.loads(token, salt=SALT, max_age=60 * 60 * 24 * 30)
        invitation = InventoryInvitation.objects.select_related("child__parent").get(
            pk=pk, child__parent__is_deleted=False
        )
    except (signing.BadSignature, InventoryInvitation.DoesNotExist, ValueError) as exc:
        raise InventoryError(
            "This assessment link is unavailable. Please ask ClearCode for a new invitation."
        ) from exc
    if invitation.revoked_at or invitation.expires_at <= timezone.now():
        raise InventoryError(
            "This assessment link has expired or was withdrawn. Please ask ClearCode for a new invitation."
        )
    return invitation


def log_activity(
    invitation: InventoryInvitation, subject: str, actor: Any = None
) -> None:
    CrmActivity.objects.create(
        lead=invitation.child.parent,
        activity_type="note",
        subject=subject,
        body=f"{subject}: Parent Reading Inventory for {invitation.child.name}.",
        created_by=actor,
    )
    AuditLog.objects.create(
        actor=actor,
        action="crm.inventory." + subject.lower().replace(" ", "_"),
        entity_type="InventoryInvitation",
        entity_id=str(invitation.pk),
    )


def queue_mail(
    invitation: InventoryInvitation,
    key: str,
    recipient: str,
    subject: str,
    body: str,
    url: str = "",
    label: str = "",
    calendar: str = "",
) -> InventoryMail:
    mail, _ = InventoryMail.objects.get_or_create(
        invitation=invitation,
        key=key,
        defaults={
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "action_url": url,
            "action_label": label,
            "calendar": calendar,
        },
    )
    return mail


def deliver_mail(pk: int) -> None:
    if settings.CRM_EMAIL_ENABLED or not settings.DEBUG:
        from apps.crm.inventory_mail import enqueue_google

        enqueue_google(pk)
        return
    # Commit the claim before contacting SMTP; concurrent requests cannot double-send.
    with transaction.atomic():
        mail = InventoryMail.objects.select_for_update().get(pk=pk)
        if mail.status != InventoryMail.Status.PENDING:
            return
        if (
            mail.action_url
            and "/reading-inventory/" in mail.action_url
            and (
                mail.invitation.revoked_at
                or mail.invitation.expires_at <= timezone.now()
            )
        ):
            mail.status, mail.error = (
                InventoryMail.Status.FAILED,
                "Assessment link expired or withdrawn. Create a new invitation.",
            )
            mail.save(update_fields=["status", "error"])
            return
        errors = newsletter_delivery_configuration_errors()
        if errors:
            mail.status, mail.error = InventoryMail.Status.FAILED, " ".join(errors)
            mail.save(update_fields=["status", "error"])
            return
        mail.status, mail.attempted_at = InventoryMail.Status.SENDING, timezone.now()
        mail.attempts += 1
        mail.save(update_fields=["status", "attempted_at", "attempts"])
    message = EmailMultiAlternatives(
        subject=mail.subject,
        body=plain_text(mail),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[mail.recipient],
        headers={"Message-ID": f"<inventory-{mail.pk}@clearcodereading.com>"},
    )
    message.attach_alternative(
        render_to_string("crm/inventory_email.html", {"email": mail}), "text/html"
    )
    if mail.calendar:
        message.attach(
            "consultation.ics", mail.calendar, "text/calendar; method=REQUEST"
        )
    try:
        sent = message.send(fail_silently=False)
    except (
        smtplib.SMTPRecipientsRefused,
        smtplib.SMTPSenderRefused,
        smtplib.SMTPAuthenticationError,
    ) as exc:
        InventoryMail.objects.filter(pk=pk).update(
            status="failed",
            error=f"Delivery rejected ({type(exc).__name__}). Check email configuration or recipient before retrying.",
        )
        return
    except (smtplib.SMTPException, OSError) as exc:
        # Transport loss may occur after acceptance. Never automatically resend it.
        InventoryMail.objects.filter(pk=pk).update(
            error=f"Delivery uncertain ({type(exc).__name__}). Check provider records before retrying."
        )
        logger.warning(
            "Inventory email delivery uncertain",
            extra={"mail_id": pk, "error_type": type(exc).__name__},
        )
        return
    if sent != 1:
        InventoryMail.objects.filter(pk=pk).update(
            status="failed", error="Email provider did not accept the message."
        )
        return
    now = timezone.now()
    InventoryMail.objects.filter(pk=pk).update(status="sent", sent_at=now, error="")
    if mail.key.startswith(("inventory_send_", "reminder-")):
        InventoryInvitation.objects.filter(
            pk=mail.invitation_id, sent_at__isnull=True
        ).update(sent_at=now)


def deliver_pending(invitation: InventoryInvitation) -> None:
    for pk in invitation.emails.filter(status="pending").values_list("pk", flat=True):
        deliver_mail(pk)


def complete_inventory(invitation: InventoryInvitation, result: dict[str, Any]) -> None:
    previous_task_id = invitation.result.get("review_task_id")
    invitation.completed_at, invitation.result = timezone.now(), result
    invitation.save()
    parent = invitation.child.parent
    review_task = CrmActivity.objects.filter(
        pk=previous_task_id, lead=parent, activity_type="task"
    ).first()
    if review_task:
        review_task.completed_at = None
        review_task.due_at = timezone.now() + timedelta(days=1)
        review_task.save(update_fields=["completed_at", "due_at"])
    else:
        review_task = CrmActivity.objects.create(
            lead=parent,
            activity_type="task",
            subject=f"Review reading inventory: {invitation.child.name}",
            body="Review the answers and follow-up in CRM → Assessments.",
            assigned_to=parent.assigned_to,
            due_at=timezone.now() + timedelta(days=1),
        )
    invitation.result["review_task_id"] = review_task.pk
    invitation.save(update_fields=["result"])
    log_activity(invitation, "Completed")
    outcome = result["outcome"]
    if outcome == "support":
        copy, url = (
            automated_copy("inventory_follow_up_support"),
            invitation_url(invitation) + "book/",
        )
    elif outcome == "resources":
        copy, url = (
            automated_copy("inventory_follow_up_resources"),
            settings.PUBLIC_APP_URL.rstrip("/") + "/resources/",
        )
    else:
        copy, url = automated_copy("inventory_follow_up_other"), ""
    values = {"child_name": invitation.child.name}
    queue_mail(
        invitation,
        f"follow-up-r{invitation.revision}" if previous_task_id else "follow-up",
        invitation.recipient,
        fill(copy["subject"], values),
        fill(copy["body"], values),
        url,
        fill(copy.get("action_label"), values) if url else "",
    )
    if parent.assigned_to and parent.assigned_to.is_active:
        copy = automated_copy("inventory_owner_review")
        queue_mail(
            invitation,
            f"owner-r{invitation.revision}" if previous_task_id else "owner",
            parent.assigned_to.email,
            fill(copy["subject"], values),
            fill(copy["body"], values),
            settings.PUBLIC_APP_URL.rstrip("/")
            + reverse("inventory_detail", args=[invitation.pk]),
            fill(copy["action_label"], values),
        )


def calendar_text(booking: Any) -> str:
    def stamp(value: Any) -> str:
        return value.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//ClearCode Reading//Consultation//EN",
            "METHOD:REQUEST",
            "BEGIN:VEVENT",
            f"UID:inventory-booking-{booking.pk}@clearcodereading.com",
            f"DTSTAMP:{stamp(timezone.now())}",
            f"DTSTART:{stamp(booking.slot.starts_at)}",
            f"DTEND:{stamp(booking.slot.ends_at)}",
            "SUMMARY:ClearCode reading consultation",
            "DESCRIPTION:Phone consultation. Contact details are available securely in the CRM.",
            f"ORGANIZER:mailto:{booking.slot.host.email}",
            f"ATTENDEE;RSVP=TRUE:mailto:{booking.invitation.recipient}",
            "STATUS:CONFIRMED",
            "SEQUENCE:0",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )
