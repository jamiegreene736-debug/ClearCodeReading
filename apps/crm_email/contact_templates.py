"""Personal and built-in email templates applied to one CRM contact.

Personal templates are private to their owner; built-in referral copy is shared.
When a template is
chosen for a contact, its ``{{token}}`` placeholders are filled with that
contact's details so the draft opens ready to review and send.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser

from apps.crm.consultation_booking import consultation_booking_url
from apps.crm.models import Lead
from apps.crm_email.automated import (
    SIGNATURE_TOKEN,
    all_copies,
    copy_for,
    fill,
    fill_html,
    specs,
    tokens,
)
from apps.crm_email.models import EmailTemplate, Mailbox, StageEmailPilot
from apps.crm_email.referral_templates import REFERRAL_TEMPLATES, ReferralTemplate
from apps.crm_email.security import clean_html

TEMPLATE_PLACEHOLDERS: Mapping[str, str] = {
    "contact.firstname": "Contact first name",
    "contact.name": "Contact full name",
    "contact.email": "Contact email address",
    "company.name": "Company, organization or school name",
    "sender.name": "Your name",
}


def contact_templates(
    sender: AbstractBaseUser,
) -> list[EmailTemplate | ReferralTemplate | TemplateOption]:
    return [
        *[
            TemplateOption("pipeline:" + copy.spec.key, copy.spec.name, copy["subject"])
            for copy in all_copies(pipeline_template_keys())
        ],
        *REFERRAL_TEMPLATES.values(),
        *EmailTemplate.objects.filter(owner_id=sender.pk),
    ]


def template_values(lead: Lead, sender: AbstractBaseUser) -> dict[str, str]:
    name = lead.contact_name.strip()
    company = lead.company.name if lead.company else ""
    sender_name = getattr(sender, "get_full_name", lambda: "")() or getattr(
        sender, "email", ""
    )
    return {
        "contact.firstname": name.split()[0] if name else "",
        "contact.name": name,
        "contact.email": lead.contact_email,
        "company.name": company or lead.organization_name or lead.school_name,
        "sender.name": sender_name,
    }


def apply_template(
    template: EmailTemplate | ReferralTemplate, lead: Lead, mailbox: Mailbox
) -> dict[str, str]:
    """Subject and body for a new draft to ``lead`` started from ``template``."""
    values = template_values(lead, mailbox.user)
    if isinstance(template, ReferralTemplate):
        name = lead.contact_name.strip()
        parts = name.split(maxsplit=1)
        if parts and parts[0].lower().rstrip(".") in {"dr", "mr", "mrs", "ms", "mx"}:
            name = parts[1] if len(parts) > 1 else ""
        values["contact.firstname"] = name.split()[0] if name else "there"
        values["contact.doctor_name"] = f"Dr. {name}" if name else "there"
        values["company.name"] = values["company.name"].strip() or (
            "your school" if template.pk == "referral-school-leader" else "your center"
        )
    return {
        "subject": fill(template.subject, values),
        "body_html": fill_html(clean_html(template.body_html), values)
        + clean_html(mailbox.signature),
    }


@dataclass(frozen=True)
class TemplateOption:
    pk: str
    name: str
    subject: str


def pipeline_template_keys() -> tuple[str, ...]:
    return tuple(
        key
        for key, spec in specs().items()
        if spec.group
        in {"Deal pipeline first-stage emails", "Pipeline introduction emails"}
    )


def apply_pipeline_template(key: str, lead: Lead, mailbox: Mailbox) -> dict[str, str]:
    """Prepare a manual draft without creating or enabling an automated delivery."""
    if key not in pipeline_template_keys():
        raise LookupError("Unknown pipeline template")
    copy = copy_for(key)
    pilot = StageEmailPilot.objects.filter(mailbox=mailbox).first()
    pilot = pilot or StageEmailPilot(mailbox=mailbox)
    values = template_values(lead, mailbox.user)
    # Multiple deals can disagree; never silently choose one investment category.
    deals = list(
        lead.opportunities.filter(
            pipeline=key.removeprefix("stage_"),
            is_deleted=False,
        ).select_related("company")
    )
    if len(deals) == 1 and deals[0].company:
        values["company.name"] = deals[0].company.name
    values.update(
        {
            "scheduling_link": (
                consultation_booking_url()
                if key in {"stage_family_enrollment", "survey_family_enrollment"}
                else pilot.scheduling_link.strip() or consultation_booking_url()
            ),
            SIGNATURE_TOKEN: pilot.bethany_signature,
            "foundation_name": pilot.foundation_name,
            "investment_category": deals[0].investment_category
            if len(deals) == 1
            else "",
        }
    )
    # The source already includes a sign-off; do not duplicate it.
    signature = clean_html(mailbox.signature)
    body = copy.html("body")
    used = tokens(copy["subject"] + body)
    for token in used:
        if token != "gmail_signature" and not values.get(token, "").strip():
            values[token] = (
                "[Missing: "
                + copy.spec.placeholders.get(
                    token, TEMPLATE_PLACEHOLDERS.get(token, token)
                )
                + "]"
            )
    values["gmail_signature"] = ""
    body_html = fill_html(clean_html(body), values)
    if "gmail_signature" in used:
        body_html += signature or "<p>[Missing: Your email signature]</p>"
    elif not any(token in used for token in (SIGNATURE_TOKEN, "foundation_name")):
        body_html += signature
    return {"subject": fill(copy["subject"], values), "body_html": body_html}
