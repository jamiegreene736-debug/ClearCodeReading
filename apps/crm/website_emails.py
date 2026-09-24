"""Branded website receipts delivered by the existing, reconciled Gmail outbox."""

from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.core.models import RecruitingInterest
from apps.crm.models import FormSubmission, NewsletterSubscription, WebsiteReceipt
from apps.crm.newsletters import _unsubscribe_url
from apps.crm.templatetags.crm_display import crm_field_label, crm_field_value
from apps.crm_email.automated import WEBSITE_TEAM_EMAIL, copy_for, fill, fill_html
from apps.crm_email.models import Mailbox, Message
from apps.crm_email.security import EmailError, mailbox_lock, require_configured
from apps.crm_email.services import active_mailbox

TEAM_EMAIL = WEBSITE_TEAM_EMAIL


def website_from_address() -> str:
    """Visible From address for website receipts; must stay on the Workspace domain."""
    configured = getattr(settings, "WEBSITE_EMAIL_FROM", TEAM_EMAIL).strip().lower()
    address = configured or TEAM_EMAIL
    if address.rsplit("@", 1)[-1] != settings.CRM_EMAIL_DOMAIN:
        raise EmailError("Website From address must use the organization email domain.")
    return address


# Receipt kinds and their form labels. The wording of each receipt lives in the
# automated-email registry (apps/crm_email/automated.py, keys "website_<kind>")
# and can be edited from CRM email settings.
# Form kinds that get a team notice only. The website already shows the visitor a
# confirmation, so no email confirmation is sent to them.
TEAM_ONLY_KINDS = frozenset({"survey"})

LABELS = {
    "consultation": "Consultation request",
    "consultation_booked": "Consultation booking",
    "assessment": "Assessment follow-up",
    "survey": "Early interest survey",
    "career": "Career interest",
    "newsletter": "Newsletter signup",
    "resources": "Family resources signup",
    "support": "Support request",
    "website": "Website inquiry",
}


def receipt_kind(submission: FormSubmission) -> str:
    if submission.submitted_data.get("consultation_booked"):
        return "consultation_booked"
    if submission.source_path == "/support/":
        return "support"
    if submission.submitted_data.get("resource_access") == "family_resources":
        return "resources"
    return submission.form_type if submission.form_type in LABELS else "website"


def receipt_context(submission: FormSubmission, *, team: bool) -> dict[str, object]:
    data = submission.submitted_data
    kind = receipt_kind(submission)
    label = LABELS[kind]
    copy = copy_for("website_team" if team else "website_" + kind)
    # Visitor-typed values are collapsed to one line so they are safe in subjects.
    values = {
        "form": label.lower(),
        "name": " ".join(str(data.get("name", "")).split()),
        "email": " ".join(str(data.get("email", "")).split()),
        "reference": str(submission.pk),
    }
    base = settings.PUBLIC_APP_URL.rstrip("/")
    rows = []
    # A receipt summarizes the request without re-emailing children's results or documents.
    keys = (
        "name",
        "email",
        "phone",
        "organization_name",
        "support_topic",
        "role_interest",
        "relationship_interests",
        "engagement_interests",
    )
    support_labels = {
        "app_access": "App or portal access",
        "technical": "Technical problem",
        "privacy": "Privacy question",
        "data_request": "Access, correction, or deletion request",
        "security": "Security concern",
        "other": "Other support request",
    }
    for key in keys:
        value = data.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "organization_name" and value in {
            "Reading assessment follow-up",
            "Website contact",
        }:
            continue
        label = (
            "Your interests"
            if key == "engagement_interests"
            else str(crm_field_label(key))
        )
        display = (
            support_labels.get(str(value), str(crm_field_value(value)))
            if key == "support_topic"
            else str(crm_field_value(value))
        )
        rows.append({"label": label, "value": display})
    if kind == "career":
        rows.append({"label": "Documents received", "value": "Résumé and cover letter"})
    if kind == "consultation_booked":
        rows.append({"label": "Consultation time", "value": str(data.get("consultation_time", ""))})
        if data.get("consultation_host"):
            rows.append({"label": "With", "value": str(data["consultation_host"])})
        if data.get("child_age_grade"):
            rows.append({"label": "Child’s age or grade", "value": str(data["child_age_grade"])})
    if team and kind in {"consultation", "consultation_booked", "website"} and data.get("notes"):
        rows.append({"label": "Message", "value": str(data["notes"])})
    if team:
        rows.extend(
            [
                {"label": "Submitted from", "value": submission.source_path},
                {
                    "label": "Received (Eastern Time)",
                    "value": timezone.localtime(submission.created_at).strftime(
                        "%Y-%m-%d %I:%M %p %Z"
                    ),
                },
                {"label": "Reference", "value": str(submission.pk)},
            ]
        )
    unsubscribe = ""
    if not team and kind == "newsletter":
        subscription = NewsletterSubscription.objects.filter(
            email=data.get("email", "")
        ).first()
        if subscription:
            unsubscribe = _unsubscribe_url(subscription)
    if team:
        action_url = base + (
            reverse(
                "admin:core_recruitinginterest_change",
                args=[data["application_id"]],
            )
            if kind == "career" and data.get("application_id")
            else reverse(
                "crm_contact_detail",
                args=[submission.lead.pk if submission.lead else None],
            )
        )
    else:
        path = fill(copy.get("action_url"), values)
        action_url = path if path.startswith("https://") else base + path
    return {
        "subject": fill(copy["subject"], values),
        "heading": fill(copy["heading"], values),
        "introduction": fill(copy.text("body"), values),
        "introduction_html": fill_html(copy.html("body"), values),
        "next_step": fill(copy.text("next_step"), values),
        "next_step_html": fill_html(copy.html("next_step"), values),
        "rows": rows,
        "action_label": fill(copy.get("action_label"), values),
        "action_url": action_url,
        "unsubscribe_url": unsubscribe,
        "team_email": TEAM_EMAIL,
        "preheader": label + " — received by ClearCode Reading",
        "reference": submission.pk,
    }


def enqueue_receipt(pk: int) -> None:
    try:
        require_configured()
        public = urlparse(settings.PUBLIC_APP_URL)
        if public.scheme != "https" or public.hostname in {
            None,
            "localhost",
            "127.0.0.1",
        }:
            raise EmailError(
                "Configure a public HTTPS website URL for form confirmations."
            )
        mailbox = (
            Mailbox.objects.select_related("user")
            .filter(
                email__iexact=settings.WEBSITE_EMAIL_SENDER,
                status=Mailbox.Status.CONNECTED,
            )
            .first()
            if settings.WEBSITE_EMAIL_SENDER
            else None
        )
        if mailbox is None:
            raise EmailError(
                "Connect the configured website sender mailbox to deliver form confirmations."
            )
        active_mailbox(mailbox.user)
        with mailbox_lock(mailbox.pk), transaction.atomic():
            active_mailbox(mailbox.user)
            receipt = WebsiteReceipt.objects.select_for_update().get(pk=pk)
            submission = receipt.submission
            if not submission.lead and not submission.submitted_data.get(
                "application_id"
            ):
                raise EmailError(
                    "The submission contact is unavailable. Review the CRM record."
                )
            application_id = submission.submitted_data.get("application_id")
            if (
                submission.form_type == "career"
                and application_id
                and not RecruitingInterest.objects.filter(pk=application_id).exists()
            ):
                raise EmailError(
                    "The recruiting application was removed; confirmations will not be sent."
                )
            kind = receipt_kind(submission)
            for team, field in ((False, "customer_message"), (True, "team_message")):
                if getattr(receipt, field + "_id"):
                    continue
                if not team and kind in TEAM_ONLY_KINDS:
                    continue
                context = receipt_context(submission, team=team)
                email = str(submission.submitted_data["email"]).strip().lower()
                message = Message.objects.create(
                    mailbox=mailbox,
                    lead=submission.lead,
                    recruiting_interest_id=submission.submitted_data.get(
                        "application_id"
                    )
                    if submission.form_type == "career"
                    else None,
                    sender=website_from_address(),
                    to=[TEAM_EMAIL if team else email],
                    reply_to=email if team else TEAM_EMAIL,
                    subject=context["subject"],
                    body_text=render_to_string("crm/website_email.txt", context),
                    body_html=render_to_string("crm/website_email.html", context),
                )
                message.rfc_message_id = (
                    f"<crm-{message.pk}@{settings.CRM_EMAIL_DOMAIN}>"
                )
                message.status = Message.Status.QUEUED
                message.save()
                setattr(receipt, field, message)
            receipt.error = ""
            receipt.save()
    except (EmailError, PermissionDenied) as exc:
        WebsiteReceipt.objects.filter(pk=pk).update(
            error=str(exc)[:255]
            if isinstance(exc, EmailError)
            else "Website sender no longer has CRM email access."
        )


def enqueue_pending_receipts() -> None:
    pending = (
        WebsiteReceipt.objects.filter(
            Q(team_message__isnull=True)
            | (
                Q(customer_message__isnull=True)
                & ~Q(submission__form_type__in=TEAM_ONLY_KINDS)
            )
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:50]
    )
    for pk in pending:
        enqueue_receipt(pk)
