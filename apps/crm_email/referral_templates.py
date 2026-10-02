"""Shared referral drafts transcribed from the four supplied partner letters."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReferralTemplate:
    pk: str
    name: str
    subject: str
    body_html: str


def _template(key: str, label: str, subject: str) -> ReferralTemplate:
    return ReferralTemplate(
        pk=f"referral-{key}",
        name=f"Referral Partner - {label}",
        subject=subject,
        body_html=(
            Path(__file__).parent / "content" / "referral_partners" / f"{key}.html"
        ).read_text(encoding="utf-8"),
    )


REFERRAL_TEMPLATES = {
    template.pk: template
    for template in (
        _template(
            "school-leader",
            "School Leader",
            "Helping {{company.name}} families with struggling readers",
        ),
        _template(
            "rec-center",
            "Rec Center",
            "Helping {{company.name}} families with struggling readers",
        ),
        _template(
            "pediatrician",
            "Pediatrician",
            "A reading support resource for your K–8 patients",
        ),
        _template(
            "general", "General", "Helping Orlando families with struggling readers"
        ),
    )
}
