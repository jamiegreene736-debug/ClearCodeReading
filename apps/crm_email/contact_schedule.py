"""Pending contact emails, including intake events awaiting outbox preparation."""

from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import connection
from django.db.models import F, Q, QuerySet
from django.db.models.functions import Greatest
from django.urls import reverse

from apps.crm.inventory_models import InventoryMail
from apps.crm.models import Lead, Opportunity, WebsiteReceipt
from apps.crm.website_emails import LABELS, TEAM_ONLY_KINDS, receipt_kind
from apps.crm_email.automated import specs
from apps.crm_email.models import Mailbox, Message, StageEmailDelivery
from apps.users.models import CustomUser


def inventory_contact_mail(prefix: str = "") -> Q:
    key = prefix + "key"
    return (
        Q(**{key + "__startswith": "inventory_send_"})
        | Q(**{key + "__startswith": "reminder-"})
        | Q(**{key + "__startswith": "follow-up"})
        | Q(**{key: "booking-parent"})
    )


def contact_scheduled_emails(user: CustomUser, lead: Lead) -> QuerySet[Message]:
    """Shared automations are manageable by a CRM admin or their sending mailbox owner."""
    automation = (
        Q(stage_delivery__isnull=False)
        | Q(website_customer_receipt__isnull=False)
        | inventory_contact_mail("inventory_delivery__")
    )
    if not user.can_manage_crm_users:
        automation &= Q(mailbox__user=user)
    personal = Q(
        mailbox__user=user,
        scheduled_at__isnull=False,
        website_team_receipt__isnull=True,
        inventory_delivery__isnull=True,
    )
    return Message.objects.filter(automation | personal, lead=lead)


def waiting_stage(user: CustomUser, lead: Lead) -> QuerySet[StageEmailDelivery]:
    rows = StageEmailDelivery.objects.filter(
        lead=lead, message__isnull=True, cancelled=False
    )
    if user.can_manage_crm_users:
        return rows
    equity_sender = Q(
        pipeline=Opportunity.Pipeline.EQUITY_INVESTMENT,
        pilot__equity_mailbox__isnull=False,
    )
    return rows.filter(
        (equity_sender & Q(pilot__equity_mailbox__user=user))
        | (~equity_sender & Q(pilot__mailbox__user=user))
    )


def waiting_receipts(user: CustomUser, lead: Lead) -> QuerySet[WebsiteReceipt]:
    rows = WebsiteReceipt.objects.filter(
        submission__lead=lead, customer_message__isnull=True, customer_cancelled=False
    ).exclude(submission__form_type__in=TEAM_ONLY_KINDS)
    if not user.can_manage_crm_users and (
        not settings.WEBSITE_EMAIL_SENDER
        or not Mailbox.objects.filter(
            user=user, email__iexact=settings.WEBSITE_EMAIL_SENDER
        ).exists()
    ):
        return rows.none()
    return rows


def waiting_inventory(user: CustomUser, lead: Lead) -> QuerySet[InventoryMail]:
    rows = InventoryMail.objects.filter(
        inventory_contact_mail(),
        invitation__child__parent=lead,
        provider_message__isnull=True,
        status=InventoryMail.Status.PENDING,
    )
    if not user.can_manage_crm_users:
        rows = rows.filter(invitation__created_by=user)
    return rows


@dataclass(frozen=True)
class UpcomingEmail:
    subject: str
    cancel_url: str
    scheduled_at: datetime | None = None
    retry_at: datetime | None = None
    preparation_error: str = ""


def pending_contact_emails(user: CustomUser, lead: Lead) -> list[UpcomingEmail]:
    if (
        getattr(connection, "schema_name", "") != "public"
        or not user.is_active
        or user.is_deleted
    ):
        return []
    queued = (
        contact_scheduled_emails(user, lead)
        .filter(status=Message.Status.QUEUED)
        .annotate(send_after=Greatest("scheduled_at", "next_attempt_at"))
        .order_by(F("send_after").asc(nulls_first=True), "created_at", "pk")
    )
    # Read the final outbox first so a concurrent handoff cannot show the email twice.
    messages = [
        UpcomingEmail(
            message.subject,
            reverse("crm_email_cancel", args=[lead.pk, message.pk]),
            message.scheduled_at,
            message.next_attempt_at
            if message.next_attempt_at == message.send_after
            else None,
        )
        for message in queued.only("id", "subject", "scheduled_at", "next_attempt_at")
    ]

    def waiting(kind: str, pk: int, subject: str, error: str = "") -> UpcomingEmail:
        return UpcomingEmail(
            subject,
            reverse("crm_email_cancel_waiting", args=[lead.pk, kind, pk]),
            preparation_error=error,
        )

    pending = []
    for delivery in waiting_stage(user, lead).order_by("pk"):
        spec = specs().get(delivery.template_key or "stage_" + delivery.pipeline)
        pending.append(
            waiting(
                "stage",
                delivery.pk,
                spec.name if spec else "Pipeline email",
                delivery.error,
            )
        )
    for receipt in (
        waiting_receipts(user, lead).select_related("submission").order_by("pk")
    ):
        pending.append(
            waiting(
                "receipt",
                receipt.pk,
                LABELS[receipt_kind(receipt.submission)] + " confirmation",
                receipt.error,
            )
        )
    for mail in waiting_inventory(user, lead):
        pending.append(waiting("inventory", mail.pk, mail.subject))
    return pending + messages
