"""Registry of every automated email the CRM sends, with editable copy.

Each automated email is described once here with its default wording. CRM
administrators can override that wording from Email settings; overrides are
stored in ``AutomatedEmail`` rows and looked up with ``copy_for`` at send time.
Placeholders use double braces, ``{{token}}``, and are filled by ``fill``.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import cast

from django.utils.html import escape

from apps.crm_email.models import AutomatedEmail
from apps.crm_email.security import plain_text

FIELD_LABELS: Mapping[str, str] = {
    "subject": "Subject",
    "heading": "Heading",
    "body": "Message",
    "next_step": "What happens next",
    "action_label": "Button label",
    "action_url": "Button link",
}
OPTIONAL_FIELDS = frozenset({"next_step", "action_label", "action_url"})
# Fields edited with the rich (HTML) editor; every other field is one line of text.
RICH_FIELDS = frozenset({"body", "next_step"})
TOKEN = re.compile(r"{{\s*([^{}]+?)\s*}}")


@dataclass(frozen=True)
class AutomatedEmailSpec:
    key: str
    group: str
    name: str
    trigger: str
    recipient: str
    defaults: Mapping[str, str]
    placeholders: Mapping[str, str] = field(default_factory=dict)
    sample: Mapping[str, str] = field(default_factory=dict)

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(name for name in FIELD_LABELS if name in self.defaults)


@dataclass(frozen=True)
class AutomatedEmailCopy:
    """Wording for one automated email.

    ``values`` holds what is stored or the default: rich fields are HTML when the
    email has been customized and plain text otherwise. Senders use ``html`` and
    ``text`` so both email parts stay in step whichever form the wording is in.
    """

    spec: AutomatedEmailSpec
    values: Mapping[str, str]
    customized: bool

    def __getitem__(self, name: str) -> str:
        return self.values[name]

    def get(self, name: str, default: str = "") -> str:
        return self.values.get(name, default)

    def is_html(self, name: str) -> bool:
        return self.customized and name in RICH_FIELDS

    def html(self, name: str) -> str:
        value = self.values.get(name, "")
        return value if self.is_html(name) else text_to_html(value)

    def text(self, name: str) -> str:
        value = self.values.get(name, "")
        return plain_text(value).strip() if self.is_html(name) else value


def tokens(text: str) -> list[str]:
    return [match.group(1).strip() for match in TOKEN.finditer(text)]


def fill(text: str, values: Mapping[str, str]) -> str:
    """Replace ``{{token}}`` placeholders; unknown tokens render as blank."""
    return TOKEN.sub(lambda match: values.get(match.group(1).strip(), ""), text)


def html_value(value: str) -> str:
    """Escape a substituted value for HTML, keeping line breaks and linking bare URLs."""
    lines = []
    for line in value.split("\n"):
        stripped = line.strip()
        if re.fullmatch(r"https://\S+", stripped):
            lines.append(f'<a href="{escape(stripped)}">{escape(stripped)}</a>')
        else:
            lines.append(escape(line))
    return "<br>".join(lines)


def fill_html(html: str, values: Mapping[str, str]) -> str:
    """Replace placeholders inside HTML; values are escaped, never interpreted."""
    return TOKEN.sub(
        lambda match: html_value(values.get(match.group(1).strip(), "")), html
    )


def text_to_html(text: str) -> str:
    """Render default plain-text wording as simple paragraphs."""
    paragraphs = [
        part.strip() for part in re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    ]
    return "".join(
        "<p>" + "<br>".join(escape(line) for line in part.split("\n")) + "</p>"
        for part in paragraphs
        if part
    )


WEBSITE_TEAM_EMAIL = "hello@clearcodereading.com"

_WEBSITE_PLACEHOLDERS = {
    "name": "Name entered on the form",
    "reference": "Submission reference number",
}
_WEBSITE_SAMPLE = {"name": "Jordan Rivera", "reference": "1042"}


def _website(
    kind: str,
    name: str,
    trigger: str,
    heading: str,
    introduction: str,
    next_step: str,
    action_label: str = "Explore ClearCode Reading",
    action_url: str = "/how-it-works/",
    extra_placeholders: Mapping[str, str] | None = None,
    extra_sample: Mapping[str, str] | None = None,
) -> AutomatedEmailSpec:
    return AutomatedEmailSpec(
        key="website_" + kind,
        group="Website form confirmations",
        name=name,
        trigger=trigger,
        recipient="The person who submitted the form",
        defaults={
            "subject": f"{name} received | ClearCode Reading",
            "heading": heading,
            "body": introduction,
            "next_step": next_step,
            "action_label": action_label,
            "action_url": action_url,
        },
        placeholders={**_WEBSITE_PLACEHOLDERS, **(extra_placeholders or {})},
        sample={**_WEBSITE_SAMPLE, **(extra_sample or {})},
    )


_INVENTORY_DISCLAIMER = (
    "\n\nThis parent inventory is a starting point for a conversation, "
    "not a diagnosis or a placement decision."
)


def _inventory(
    key: str,
    name: str,
    trigger: str,
    recipient: str,
    subject: str,
    body: str,
    action_label: str = "",
    placeholders: Mapping[str, str] | None = None,
    sample: Mapping[str, str] | None = None,
) -> AutomatedEmailSpec:
    defaults = {"subject": subject, "body": body}
    if action_label:
        defaults["action_label"] = action_label
    return AutomatedEmailSpec(
        key="inventory_" + key,
        group="Parent Reading Inventory",
        name=name,
        trigger=trigger,
        recipient=recipient,
        defaults=defaults,
        placeholders=placeholders or {},
        sample=sample or {},
    )


@lru_cache(maxsize=1)
def first_stage_source() -> dict[str, dict[str, object]]:
    return cast(
        dict[str, dict[str, object]],
        json.loads(Path(__file__).with_name("first_stage_copy.json").read_text()),
    )


_STAGE_PLACEHOLDERS = {
    "contact.firstname": "Contact first name",
    "company.name": "Company name",
    "scheduling_link": "Bethany’s scheduling link",
    "Bethany’s email signature": "Bethany’s email signature",
    "investment_category": "Investment category",
    "foundation_name": "Foundation sender name",
    "gmail_signature": "Equity sender signature",
}
_STAGE_SAMPLE = {
    "contact.firstname": "Jordan",
    "company.name": "Example Organization",
    "scheduling_link": "https://clearcodereading.com/book/",
    "Bethany’s email signature": "Bethany Fleming\nFounder & CEO, ClearCode Reading Center",
    "investment_category": "education",
    "foundation_name": "Bethany Fleming",
    "gmail_signature": "ClearCode, Inc.",
}
_STAGE_NAMES = {
    "family_enrollment": "Families & Enrollment",
    "referral_partners": "Referral Partners",
    "foundation_donors": "Foundation Donors",
    "foundation_grants": "Foundation Grants & PRIs",
    "equity_investment": "Equity & Investment",
}


def _stage(pipeline: str) -> AutomatedEmailSpec:
    source = first_stage_source()[pipeline]
    body = "\n\n".join(cast(list[str], source["paragraphs"]))
    body = body.replace("[Name]", "{{foundation_name}}")
    body = body.replace("Signature block from Gmail", "{{gmail_signature}}")
    used = set(tokens(body))
    return AutomatedEmailSpec(
        key="stage_" + pipeline,
        group="Deal pipeline first-stage emails",
        name=_STAGE_NAMES[pipeline] + " first-stage email",
        trigger=(
            "A deal enters the first stage of the "
            + _STAGE_NAMES[pipeline]
            + " pipeline (currently delivered only to the internal test inbox)"
        ),
        recipient="The deal’s contact",
        defaults={"subject": str(source["subject"]), "body": body},
        placeholders={k: v for k, v in _STAGE_PLACEHOLDERS.items() if k in used},
        sample={k: v for k, v in _STAGE_SAMPLE.items() if k in used},
    )


def _survey_family() -> AutomatedEmailSpec:
    base = _stage("family_enrollment")
    return AutomatedEmailSpec(
        key="survey_family_enrollment",
        group="Survey initial emails",
        name="Survey: Families & Enrollment",
        trigger=(
            "A family completes the early interest survey and enters the Families & "
            "Enrollment pipeline (currently delivered only to the internal test inbox)"
        ),
        recipient="The survey respondent",
        defaults=base.defaults,
        placeholders=base.placeholders,
        sample=base.sample,
    )


def _build_specs() -> tuple[AutomatedEmailSpec, ...]:
    return (
        _website(
            "consultation",
            "Consultation request",
            "A visitor requests a consultation on the website",
            "Let’s find a clear next step.",
            "Thank you for requesting a consultation with ClearCode Reading.",
            "Our team will review your request and contact you to arrange a conversation about your family’s reading goals. Your appointment is not booked yet.",
        ),
        _website(
            "consultation_booked",
            "Consultation booking",
            "A visitor books a phone consultation on the public booking page",
            "Your consultation is booked.",
            "Thank you for booking a phone consultation with ClearCode Reading.",
            "We will call the phone number you provided at the time shown below. If you need to change the time, reply to this email or contact our team.",
        ),
        _website(
            "assessment",
            "Assessment follow-up",
            "A visitor completes the website reading check-in",
            "Your reading follow-up is with us.",
            "Thank you for sharing your reading check-in with ClearCode Reading.",
            "Our team will review what you shared and contact you about appropriate next steps. This check-in is not a diagnosis or a confirmed enrollment.",
        ),
        _website(
            "survey",
            "Early interest survey",
            "A visitor submits the early interest survey",
            "Thank you for helping shape what’s next.",
            "We’ve received your early interest survey and the ways you’d like to connect with ClearCode Reading.",
            "{{interest_follow_up}}",
            extra_placeholders={
                "interest_follow_up": "Sentences generated from the interests the visitor selected"
            },
            extra_sample={
                "interest_follow_up": "We’ve recorded your priority enrollment waitlist interest; a place is not reserved yet."
            },
        ),
        _website(
            "career",
            "Career interest",
            "A visitor submits the careers form with a résumé and cover letter",
            "Thank you for your interest in our team.",
            "We’ve received your career interest form, résumé, and cover letter.",
            "Our recruiting team will review your application and contact you if there is a suitable next step. No interview or position is confirmed by this receipt.",
            "Explore careers",
            "/careers/",
        ),
        _website(
            "newsletter",
            "Newsletter signup",
            "A visitor signs up for the newsletter",
            "You’re on the list.",
            "Welcome to the ClearCode Reading newsletter. Your signup is confirmed.",
            "Look out for reading resources and news from ClearCode Reading. You can unsubscribe using the link below.",
            "Read our latest articles",
            "/blog/",
        ),
        _website(
            "resources",
            "Family resources signup",
            "A visitor unlocks the free family resources",
            "Your next reading step starts here.",
            "Thank you for signing up for ClearCode Reading’s free family resources.",
            "Your resources are available in the browser where you signed up. If you return on another device, simply complete the short access form again.",
            "Explore family resources",
            "/resources/",
        ),
        _website(
            "support",
            "Support request",
            "A visitor submits the support form",
            "We’ve received your support request.",
            "Thank you for contacting ClearCode Reading support.",
            "Our team will review the topic and details you submitted and reply about next steps. This email confirms receipt; it does not mean the issue is resolved.",
            "Visit support",
            "/support/",
        ),
        _website(
            "website",
            "Website inquiry",
            "A visitor submits any other website contact form",
            "Thank you for reaching out.",
            "Your message has reached the ClearCode Reading team.",
            "We’ll review your inquiry and follow up using the contact details you provided.",
        ),
        AutomatedEmailSpec(
            key="website_team",
            group="Website team notifications",
            name="New website submission notice",
            trigger="Every website form submission, sent alongside the visitor’s confirmation",
            recipient=WEBSITE_TEAM_EMAIL,
            defaults={
                "subject": "New {{form}} · #{{reference}} | ClearCode Reading",
                "heading": "New {{form}}",
                "body": "A website visitor has submitted the form below. Review the full submission in the CRM before following up.",
                "next_step": "Reply to this email to contact the person who submitted the form. Full responses and any sensitive details remain in the secured record.",
                "action_label": "Review submission",
            },
            placeholders={
                "form": "Form name, in lower case (for example “consultation request”)",
                "name": "Name entered on the form",
                "email": "Email address entered on the form",
                "reference": "Submission reference number",
            },
            sample={
                "form": "consultation request",
                "name": "Jordan Rivera",
                "email": "jordan@example.com",
                "reference": "1042",
            },
        ),
        _inventory(
            "invitation",
            "Assessment invitation",
            "Suggested wording when a team member sends a Parent Reading Inventory; it can still be edited before each send",
            "The parent",
            "Your ClearCode Parent Reading Inventory",
            "Hi {{parent_name}},\n\nPlease complete our Parent Reading Inventory to help us understand your child’s reading. You can save your answers and return using the same link.\n\nThank you,\nThe ClearCode Reading team",
            "Complete assessment",
            {"parent_name": "Parent’s name from the contact record"},
            {"parent_name": "Jordan Rivera"},
        ),
        _inventory(
            "reminder",
            "Assessment reminder",
            "A team member requests a reminder for an unfinished inventory",
            "The parent",
            "Reminder: your Parent Reading Inventory",
            "You can complete or continue your Parent Reading Inventory using the link below.",
            "Continue assessment",
            {"child_name": "Child’s name"},
            {"child_name": "Sam"},
        ),
        _inventory(
            "follow_up_support",
            "Inventory results: support suggested",
            "A parent completes the inventory and the answers suggest reading support",
            "The parent",
            "Your Parent Reading Inventory: next steps",
            "Thank you for completing the Parent Reading Inventory. Your responses suggest that a conversation about reading support may be helpful. You can choose a consultation time using the link below."
            + _INVENTORY_DISCLAIMER,
            "Schedule a consultation",
            {"child_name": "Child’s name"},
            {"child_name": "Sam"},
        ),
        _inventory(
            "follow_up_resources",
            "Inventory results: resources suggested",
            "A parent completes the inventory and the answers suggest home resources",
            "The parent",
            "Your Parent Reading Inventory: next steps",
            "Thank you for completing the Parent Reading Inventory. Explore our reading resources for ideas to support continued practice. Our team will review your responses and can help you choose next steps."
            + _INVENTORY_DISCLAIMER,
            "Explore reading resources",
            {"child_name": "Child’s name"},
            {"child_name": "Sam"},
        ),
        _inventory(
            "follow_up_other",
            "Inventory results: team review",
            "A parent completes the inventory with any other outcome",
            "The parent",
            "Your Parent Reading Inventory: next steps",
            "Thank you for completing the Parent Reading Inventory. Our team will review your responses and contact you about the next step."
            + _INVENTORY_DISCLAIMER,
            "",
            {"child_name": "Child’s name"},
            {"child_name": "Sam"},
        ),
        _inventory(
            "owner_review",
            "Inventory ready for review",
            "A parent completes the inventory and the contact has an assigned owner",
            "The contact’s assigned team member",
            "A reading inventory is ready for review",
            "An assigned contact has completed the Parent Reading Inventory.",
            "Review assessment",
            {"child_name": "Child’s name"},
            {"child_name": "Sam"},
        ),
        _inventory(
            "booking_parent",
            "Consultation booked (parent)",
            "A parent books a consultation from their inventory results",
            "The parent",
            "Your ClearCode consultation is booked",
            "Your phone consultation is booked for {{appointment}}. We will call the phone number you provided. A calendar invitation is attached.",
            "",
            {"appointment": "Appointment date and time in the parent’s time zone"},
            {"appointment": "Tuesday, March 03 at 10:00 AM EST"},
        ),
        _inventory(
            "booking_host",
            "Consultation booked (host)",
            "A parent books a consultation from their inventory results",
            "The team member hosting the consultation",
            "A ClearCode consultation is booked",
            "A parent has booked a consultation for {{appointment}}. Open the assessment for contact details.",
            "View booking",
            {"appointment": "Appointment date and time in the parent’s time zone"},
            {"appointment": "Tuesday, March 03 at 10:00 AM EST"},
        ),
        *(_stage(pipeline) for pipeline in _STAGE_NAMES),
        _survey_family(),
        AutomatedEmailSpec(
            key="survey_general",
            group="Survey initial emails",
            name="Survey: all other pipelines",
            trigger=(
                "The early interest survey routes a contact to any pipeline other than "
                "Families & Enrollment (referral partners, donors, investors and so on); "
                "one email is sent even when several pipelines are selected. "
                "Draft wording until the approved copy is posted"
            ),
            recipient="The survey respondent (currently delivered only to the internal test inbox)",
            defaults={
                "subject": "Thanks for connecting with ClearCode Reading Center",
                "body": (
                    "Hi {{contact.firstname}},\n\n"
                    "Thank you for completing our early interest survey and for telling us how "
                    "you’d like to connect with ClearCode Reading Center.\n\n"
                    "I’m Bethany Fleming, Founder & CEO of ClearCode Reading Center. We’re a "
                    "structured literacy intervention center opening in the Orlando area in 2027, "
                    "built for K–8 students who haven’t yet reached grade-level reading proficiency. "
                    "Whether you’re interested in referring families, supporting our foundation, "
                    "investing, or partnering with us in another way, I’d love to talk.\n\n"
                    "Grab a time on my calendar that works for you: {{scheduling_link}}\n\n"
                    "Warmly,\n"
                    "{{Bethany’s email signature}}"
                ),
            },
            placeholders={
                key: _STAGE_PLACEHOLDERS[key]
                for key in (
                    "contact.firstname",
                    "company.name",
                    "scheduling_link",
                    "Bethany’s email signature",
                )
            },
            sample={
                key: _STAGE_SAMPLE[key]
                for key in (
                    "contact.firstname",
                    "company.name",
                    "scheduling_link",
                    "Bethany’s email signature",
                )
            },
        ),
        AutomatedEmailSpec(
            key="account_invitation",
            group="Team accounts",
            name="Account invitation",
            trigger="An administrator creates or re-invites a team member or portal user",
            recipient="The invited person",
            defaults={
                "subject": "Your ClearCode Reading account is ready",
                "body": (
                    "Hello {{first_name}},\n\n"
                    "Your ClearCode Reading account has been created.\n"
                    "Email / login: {{email}}\nAccount type: {{role}}\n\n"
                    "Choose your password and finish setting up your account:\n{{setup_link}}\n\n"
                    "This private link expires in {{expires_hours}} hours and can be used once. "
                    "If it expires, ask your administrator to resend your invitation.\n\n"
                    "After setup, log in here: {{login_link}}\n"
                    "{{crm_note}}"
                ),
            },
            placeholders={
                "first_name": "First name, or the email address when no name is set",
                "email": "Login email address",
                "role": "Account type",
                "setup_link": "One-time password setup link",
                "expires_hours": "Hours until the setup link expires",
                "login_link": "Login page address",
                "crm_note": "For CRM users only: a note that Gmail is connected on first login",
            },
            sample={
                "first_name": "Jordan",
                "email": "jordan@clearcodereading.com",
                "role": "Backend employee",
                "setup_link": "https://clearcodereading.com/accounts/invitation/…",
                "expires_hours": "24",
                "login_link": "https://clearcodereading.com/accounts/login/",
                "crm_note": "\nOn your first login, we will help you connect your work Gmail account.\n",
            },
        ),
    )


@lru_cache(maxsize=1)
def specs() -> Mapping[str, AutomatedEmailSpec]:
    return {spec.key: spec for spec in _build_specs()}


def spec_for(key: str) -> AutomatedEmailSpec:
    try:
        return specs()[key]
    except KeyError as exc:
        raise LookupError(f"Unknown automated email: {key}") from exc


def groups() -> list[str]:
    seen: list[str] = []
    for spec in specs().values():
        if spec.group not in seen:
            seen.append(spec.group)
    return seen


def _copy(spec: AutomatedEmailSpec, row: AutomatedEmail | None) -> AutomatedEmailCopy:
    values = {
        name: (getattr(row, name) if row is not None else spec.defaults[name])
        for name in spec.fields
    }
    return AutomatedEmailCopy(spec, values, row is not None)


def copy_for(key: str) -> AutomatedEmailCopy:
    """Current wording for one automated email: the saved override or the default."""
    spec = spec_for(key)
    return _copy(spec, AutomatedEmail.objects.filter(key=key).first())


def all_copies(keys: Iterable[str] | None = None) -> list[AutomatedEmailCopy]:
    wanted = list(keys) if keys is not None else list(specs())
    rows = {row.key: row for row in AutomatedEmail.objects.filter(key__in=wanted)}
    return [_copy(spec_for(key), rows.get(key)) for key in wanted]


def preview(copy: AutomatedEmailCopy) -> dict[str, str]:
    """Sample-filled wording: HTML for rich fields, text for the rest."""
    return {
        name: fill_html(copy.html(name), copy.spec.sample)
        if name in RICH_FIELDS
        else fill(copy[name], copy.spec.sample)
        for name in copy.spec.fields
    }
