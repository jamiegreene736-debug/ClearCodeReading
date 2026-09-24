"""First-stage approved copy and an internal test pilot using the durable Gmail outbox."""

import re
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.crm.consultation_booking import consultation_booking_url
from apps.crm.models import Opportunity
from apps.crm_email.automated import TOKEN, copy_for, first_stage_source, html_value
from apps.crm_email.models import Mailbox, Message, StageEmailDelivery, StageEmailPilot
from apps.crm_email.security import (
    EmailError,
    mailbox_lock,
    plain_text,
    require_configured,
)
from apps.crm_email.services import active_mailbox

TEST_RECIPIENT = "info@clearcodereading.com"


@dataclass(frozen=True)
class StageCopy:
    subject: str
    body: str
    body_html: str
    missing: tuple[str, ...]
    source: str
    key: str = ""


def sending_mailbox(pipeline: str, pilot: StageEmailPilot) -> Mailbox:
    if pipeline == Opportunity.Pipeline.EQUITY_INVESTMENT and pilot.equity_mailbox:
        return pilot.equity_mailbox
    return pilot.mailbox


def template_key(deal: Opportunity, delivery: StageEmailDelivery | None = None) -> str:
    """Registry key for a deal's first-stage email; survey entries use their own copy."""
    if delivery is not None and delivery.template_key:
        return delivery.template_key
    return "stage_" + deal.pipeline


def render_copy(deal: Opportunity, pilot: StageEmailPilot, key: str = "") -> StageCopy:
    # Approved wording, or the administrator's edit from CRM email settings.
    key = key or template_key(deal)
    copy = copy_for(key)
    contact = deal.lead
    name = (
        contact.contact_name.strip().split()[0]
        if contact and contact.contact_name.strip()
        else ""
    )
    company = (
        deal.company.name
        if deal.company
        else (
            contact.company.name
            if contact and contact.company
            else contact.organization_name
            if contact
            else ""
        )
    )
    values = {
        "contact.firstname": name,
        "company.name": company,
        "scheduling_link": pilot.scheduling_link or consultation_booking_url(),
        "Bethany’s email signature": pilot.bethany_signature,
        "investment_category": deal.investment_category,
        "foundation_name": pilot.foundation_name,
        "gmail_signature": pilot.equity_signature
        or plain_text(sending_mailbox(deal.pipeline, pilot).signature),
    }
    labels = {
        "contact.firstname": "Contact first name",
        "company.name": "Company name",
        "scheduling_link": "Bethany’s scheduling link",
        "Bethany’s email signature": "Bethany’s email signature",
        "investment_category": "Investment category",
        "foundation_name": "Foundation sender name",
        "gmail_signature": "Equity sender signature",
    }
    missing: list[str] = []

    def resolve(token: str) -> str:
        value = values.get(token, "").strip()
        if not value or "{{" in value or "}}" in value or "[Name]" in value:
            label = labels.get(token, token)
            if label not in missing:
                missing.append(label)
            return f"[Missing: {label}]"
        return value

    def substitute(match: re.Match[str]) -> str:
        return resolve(match.group(1).strip())

    def substitute_html(match: re.Match[str]) -> str:
        return html_value(resolve(match.group(1).strip()))

    body = TOKEN.sub(substitute, copy.text("body"))
    body_html = TOKEN.sub(substitute_html, copy.html("body"))
    subject = TOKEN.sub(substitute, copy["subject"])
    if copy.customized:
        source = "Edited in CRM email settings"
    elif key.startswith("survey_"):
        source = "Pipeline introduction default wording"
    else:
        source = str(first_stage_source()[deal.pipeline]["source"])
    return StageCopy(subject, body, body_html, tuple(missing), source, key)


def pilot_allowed(delivery: StageEmailDelivery) -> bool:
    deal, pilot = delivery.deal, delivery.pilot
    mailbox = sending_mailbox(delivery.pipeline, pilot)
    return bool(
        pilot.enabled
        and not delivery.cancelled
        and mailbox.user.is_active
        and not mailbox.user.is_deleted
        and mailbox.user.has_crm_access
        and mailbox.status == "connected"
        and mailbox.email
        and mailbox.email.lower() == mailbox.user.email.lower()
        and not deal.is_deleted
        and deal.lead
        and not deal.lead.is_deleted
        and deal.lead.contact_email.strip().lower() == TEST_RECIPIENT
        and deal.pipeline == delivery.pipeline
        and deal.stage == Opportunity.initial_stage_for_pipeline(delivery.pipeline)
    )


def stage_send_allowed(message: Message) -> bool:
    delivery = (
        StageEmailDelivery.objects.select_related(
            "deal__lead", "pilot__mailbox__user", "pilot__equity_mailbox__user"
        )
        .filter(message=message)
        .first()
    )
    if delivery is None:
        return True
    return bool(
        pilot_allowed(delivery)
        and message.mailbox_id == sending_mailbox(delivery.pipeline, delivery.pilot).pk
        and message.to == [TEST_RECIPIENT]
        and not message.cc
        and not message.bcc
        and message.subject.startswith("[TEST] ")
    )


def enqueue_delivery(pk: int) -> None:
    delivery = StageEmailDelivery.objects.select_related("pilot").get(pk=pk)
    try:
        require_configured()
        with (
            mailbox_lock(sending_mailbox(delivery.pipeline, delivery.pilot).pk),
            transaction.atomic(),
        ):
            delivery = (
                StageEmailDelivery.objects.select_for_update(of=("self",))
                .select_related(
                    "deal__lead__company",
                    "deal__company",
                    "pilot__mailbox__user",
                    "pilot__equity_mailbox__user",
                )
                .get(pk=pk)
            )
            if delivery.message_id or delivery.cancelled:
                return
            if not pilot_allowed(delivery):
                delivery.cancelled = True
                delivery.error = "Test paused, recipient changed, or deal left its first stage. Create a new test deal to try again."
                delivery.save(update_fields=["cancelled", "error"])
                return
            pilot = delivery.pilot
            mailbox = active_mailbox(sending_mailbox(delivery.pipeline, pilot).user)
            copy = render_copy(
                delivery.deal, pilot, template_key(delivery.deal, delivery)
            )
            if copy.missing:
                raise EmailError("Complete before sending: " + ", ".join(copy.missing))
            message = Message.objects.create(
                mailbox=mailbox,
                lead=delivery.deal.lead,
                sender=mailbox.email,
                to=[TEST_RECIPIENT],
                subject="[TEST] " + copy.subject,
                body_text=copy.body,
                body_html=copy.body_html,
                status=Message.Status.QUEUED,
            )
            message.rfc_message_id = f"<crm-{message.pk}@{settings.CRM_EMAIL_DOMAIN}>"
            message.save(update_fields=["rfc_message_id"])
            delivery.message = message
            delivery.error = ""
            delivery.save(update_fields=["message", "error"])
    except (EmailError, PermissionDenied) as exc:
        StageEmailDelivery.objects.filter(pk=pk).update(
            error=str(exc)[:255]
            if isinstance(exc, EmailError)
            else "Test sender no longer has CRM access."
        )


def enqueue_stage_emails() -> None:
    for pk in (
        StageEmailDelivery.objects.filter(message__isnull=True, cancelled=False)
        .order_by("pk")
        .values_list("pk", flat=True)[:50]
    ):
        enqueue_delivery(pk)
