"""Personal and built-in email templates applied to one CRM contact.

Personal templates are private to their owner; built-in referral copy is shared.
When a template is
chosen for a contact, its ``{{token}}`` placeholders are filled with that
contact's details so the draft opens ready to review and send.
"""

from collections.abc import Mapping

from django.contrib.auth.models import AbstractBaseUser

from apps.crm.models import Lead
from apps.crm_email.automated import fill, fill_html
from apps.crm_email.models import EmailTemplate, Mailbox
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
) -> list[EmailTemplate | ReferralTemplate]:
    return [
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
