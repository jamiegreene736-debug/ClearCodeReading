"""Branded website receipts delivered by the existing, reconciled Gmail outbox."""

from dataclasses import dataclass
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
from apps.crm_email.models import Mailbox, Message
from apps.crm_email.security import EmailError, mailbox_lock, require_configured
from apps.crm_email.services import active_mailbox

TEAM_EMAIL = "info@clearcodereading.com"


@dataclass(frozen=True)
class ReceiptCopy:
    label: str
    heading: str
    introduction: str
    next_step: str
    action_label: str = "Explore ClearCode Reading"
    action_path: str = "/how-it-works/"


COPY = {
    "consultation": ReceiptCopy(
        "Consultation request",
        "Let’s find a clear next step.",
        "Thank you for requesting a consultation with ClearCode Reading.",
        "Our team will review your request and contact you to arrange a conversation about your family’s reading goals. Your appointment is not booked yet.",
    ),
    "assessment": ReceiptCopy(
        "Assessment follow-up",
        "Your reading follow-up is with us.",
        "Thank you for sharing your reading check-in with ClearCode Reading.",
        "Our team will review what you shared and contact you about appropriate next steps. This check-in is not a diagnosis or a confirmed enrollment.",
    ),
    "survey": ReceiptCopy(
        "Early interest survey",
        "Thank you for helping shape what’s next.",
        "We’ve received your early interest survey and the ways you’d like to connect with ClearCode Reading.",
        "We’ll use your selected interests to guide relevant follow-up. Waitlist interest does not reserve a place, and a consultation request does not book an appointment.",
    ),
    "career": ReceiptCopy(
        "Career interest",
        "Thank you for your interest in our team.",
        "We’ve received your career interest form, résumé, and cover letter.",
        "Our recruiting team will review your application and contact you if there is a suitable next step. No interview or position is confirmed by this receipt.",
        "Explore careers",
        "/careers/",
    ),
    "newsletter": ReceiptCopy(
        "Newsletter signup",
        "You’re on the list.",
        "Welcome to the ClearCode Reading newsletter. Your signup is confirmed.",
        "Look out for reading resources and news from ClearCode Reading. You can unsubscribe using the link below.",
        "Read our latest articles",
        "/blog/",
    ),
    "resources": ReceiptCopy(
        "Family resources signup",
        "Your next reading step starts here.",
        "Thank you for signing up for ClearCode Reading’s free family resources.",
        "Your resources are available in the browser where you signed up. If you return on another device, simply complete the short access form again.",
        "Explore family resources",
        "/resources/",
    ),
    "support": ReceiptCopy(
        "Support request",
        "We’ve received your support request.",
        "Thank you for contacting ClearCode Reading support.",
        "Our team will review the topic and details you submitted and reply about next steps. This email confirms receipt; it does not mean the issue is resolved.",
        "Visit support",
        "/support/",
    ),
    "website": ReceiptCopy(
        "Website inquiry",
        "Thank you for reaching out.",
        "Your message has reached the ClearCode Reading team.",
        "We’ll review your inquiry and follow up using the contact details you provided.",
    ),
}


def receipt_kind(submission: FormSubmission) -> str:
    if submission.source_path == "/support/":
        return "support"
    if submission.submitted_data.get("resource_access") == "family_resources":
        return "resources"
    return submission.form_type if submission.form_type in COPY else "website"


def receipt_context(submission: FormSubmission, *, team: bool) -> dict[str, object]:
    data = submission.submitted_data
    kind = receipt_kind(submission)
    copy = COPY[kind]
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
    if team and kind in {"consultation", "website"} and data.get("notes"):
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
    next_step = copy.next_step
    if kind == "survey":
        interests = data.get("engagement_interests", [])
        steps = []
        if "priority_waitlist" in interests:
            steps.append(
                "We’ve recorded your priority enrollment waitlist interest; a place is not reserved yet."
            )
        if "consultation" in interests:
            steps.append(
                "Our team will contact you to arrange your requested free consultation; an appointment is not booked yet."
            )
        if "career_interest" in interests:
            steps.append(
                "We’ve noted your interest in working with ClearCode. You can submit your résumé and cover letter on our Careers page."
            )
        if any(
            item in interests
            for item in ("community_partner", "refer_family", "professional_connection", "referral_partner", "donor")
        ):
            steps.append(
                "We’ve noted your interest in connecting with our community team."
            )
        if any(item in interests for item in ("opening_updates", "general_email")):
            steps.append(
                "We’ll keep you informed with relevant ClearCode Reading updates."
            )
        next_step = (
            " ".join(steps) or "We’ll follow up based on the interests you selected."
        )
    unsubscribe = ""
    if not team and kind in {"newsletter", "survey"}:
        subscription = NewsletterSubscription.objects.filter(
            email=data.get("email", "")
        ).first()
        if subscription:
            unsubscribe = _unsubscribe_url(subscription)
    return {
        "subject": f"New {copy.label.lower()} · #{submission.pk} | ClearCode Reading"
        if team
        else f"{copy.label} received | ClearCode Reading",
        "heading": f"New {copy.label.lower()}" if team else copy.heading,
        "introduction": "A website visitor has submitted the form below. Review the full submission in the CRM before following up."
        if team
        else copy.introduction,
        "next_step": "Reply to this email to contact the person who submitted the form. Full responses and any sensitive details remain in the secured record."
        if team
        else next_step,
        "rows": rows,
        "action_label": "Review submission" if team else copy.action_label,
        "action_url": base
        + (
            (
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
            if team
            else copy.action_path
        ),
        "unsubscribe_url": unsubscribe,
        "team_email": TEAM_EMAIL,
        "preheader": copy.label + " — received by ClearCode Reading",
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
            for team, field in ((False, "customer_message"), (True, "team_message")):
                if getattr(receipt, field + "_id"):
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
                    sender=mailbox.email,
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
            Q(customer_message__isnull=True) | Q(team_message__isnull=True)
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:50]
    )
    for pk in pending:
        enqueue_receipt(pk)
