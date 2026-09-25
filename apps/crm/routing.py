"""Queue helpers for inquiries that still need a pipeline decision."""

from django.db.models import QuerySet
from django.utils import timezone

from apps.crm.models import IntakeTriage, Lead
from apps.crm.services import partner_interest_is_selected
from apps.crm.surveys import SURVEY_ENGAGEMENTS


SHORT_SIGNAL_LABELS = {
    "referral_partner": "Referral partner",
    "donor": "Donor",
    "advocate": "Advocate",
    "community_partner": "Community partner",
    "refer_family": "Family referral",
    "professional_connection": "Professional connection",
    "career_interest": "Career interest",
    "opening_updates": "Opening updates",
    "priority_waitlist": "Waitlist",
    "consultation": "Consultation",
    "general_email": "Email list",
}


def pending_routing_queryset() -> QuerySet:
    return IntakeTriage.objects.filter(
        status=IntakeTriage.Status.PENDING,
        lead__is_deleted=False,
    )


def pending_routing_people_count() -> int:
    return pending_routing_queryset().values("lead_id").distinct().count()


def waiting_label(created_at, now=None) -> str:
    now = now or timezone.now()
    minutes = max(int((now - created_at).total_seconds() // 60), 0)
    if minutes < 1:
        return "Just arrived"
    if minutes < 60:
        return f"{minutes}m waiting"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h waiting"
    days = hours // 24
    unit = "day" if days == 1 else "days"
    return f"{days} {unit} waiting"


def intake_signal_labels(submitted_data) -> list[str]:
    if not isinstance(submitted_data, dict):
        return []
    labels = []
    seen = set()
    relationship = dict(Lead.RelationshipInterest.choices)
    for key, source in (
        ("relationship_interests", relationship),
        ("engagement_interests", {**SURVEY_ENGAGEMENTS, **SHORT_SIGNAL_LABELS}),
    ):
        values = submitted_data.get(key) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values:
            label = SHORT_SIGNAL_LABELS.get(value) or source.get(value)
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
    if not labels and partner_interest_is_selected(submitted_data.get("partner_interest")):
        labels.append("Partner interest")
    return labels


def routing_queue(now=None):
    """Group pending inquiries by person, oldest person first."""
    now = now or timezone.now()
    items = list(
        pending_routing_queryset()
        .select_related("lead__company", "submission")
        .order_by("created_at")
    )
    people = []
    by_lead = {}
    for item in items:
        person = by_lead.get(item.lead_id)
        if person is None:
            person = {
                "lead": item.lead,
                "items": [],
                "signals": [],
                "waiting_since": item.created_at,
            }
            by_lead[item.lead_id] = person
            people.append(person)
        person["items"].append(item)
        for label in intake_signal_labels(item.submission.submitted_data):
            if label not in person["signals"]:
                person["signals"].append(label)
    for person in people:
        person["waiting_label"] = waiting_label(person["waiting_since"], now)
        person["inquiry_count"] = len(person["items"])
    oldest = people[0]["waiting_label"] if people else ""
    return {
        "people": people,
        "people_count": len(people),
        "inquiry_count": len(items),
        "oldest_waiting": oldest,
    }
