"""Personal email templates applied to one CRM contact.

Templates are private to their owner (see ``EmailTemplate``). When a template is
chosen for a contact, its ``{{token}}`` placeholders are filled with that
contact's details so the draft opens ready to review and send.
"""

from collections.abc import Mapping

from django.contrib.auth.models import AbstractBaseUser

from apps.crm.models import Lead
from apps.crm_email.automated import fill, fill_html
from apps.crm_email.models import EmailTemplate, Mailbox
from apps.crm_email.security import clean_html

TEMPLATE_PLACEHOLDERS: Mapping[str, str] = {
    "contact.firstname": "Contact first name",
    "contact.name": "Contact full name",
    "contact.email": "Contact email address",
    "company.name": "Company, organization or school name",
    "sender.name": "Your name",
}


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
    template: EmailTemplate, lead: Lead, mailbox: Mailbox
) -> dict[str, str]:
    """Subject and body for a new draft to ``lead`` started from ``template``."""
    values = template_values(lead, mailbox.user)
    return {
        "subject": fill(template.subject, values),
        "body_html": fill_html(clean_html(template.body_html), values)
        + clean_html(mailbox.signature),
    }
