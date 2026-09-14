"""Single-owner hiring rules; no second approval and no external message sends."""

from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.core.models import RecruitingInterest
from apps.crm.access import crm_owner_queryset
from apps.crm.hiring_models import HiringCandidate, HiringEvent
from apps.users.models import CustomUser

CHECKLISTS = {
    "application": [
        ("application_reviewed", "Application and available documents reviewed")
    ],
    "screening": [
        ("qualifications", "Qualifications meet the role requirements"),
        ("availability", "Availability and role fit confirmed"),
    ],
    "interview": [("evaluation", "Interview / teaching demonstration completed")],
    "onboarding": [
        ("paperwork", "Required paperwork completed"),
        ("training", "Required training completed"),
        ("readiness", "I confirm this teacher is ready for assignment"),
    ],
}
ACTIVE_STAGES = [value for value, _ in HiringCandidate.Stage.choices][:7]
DEFAULT_ACTIONS = {
    "application": "Review application",
    "screening": "Complete initial screening",
    "interview": "Schedule interview / teaching demonstration",
    "decision": "Record hiring decision",
    "offer": "Follow up on offer",
    "onboarding": "Complete onboarding checklist",
    "hold": "Review hold",
}


def hiring_owner_queryset() -> QuerySet[CustomUser]:
    return crm_owner_queryset().filter(
        Q(is_superuser=True)
        | Q(is_staff=True)
        | Q(role=CustomUser.Role.SUPER_ADMIN)
        | Q(hiring_enabled=True)
    )


def business_due_date(start: date | None = None) -> date:
    result = start or timezone.localdate()
    remaining = 2
    while remaining:
        result += timedelta(days=1)
        if result.weekday() < 5:
            remaining -= 1
    return result


def initialize_candidate(application: RecruitingInterest) -> HiringCandidate:
    """Idempotently enroll an actual teacher application, preserving its owner."""
    candidate, created = HiringCandidate.objects.get_or_create(
        application=application,
        defaults={
            "due_date": business_due_date(),
            "stage_entered_at": application.created_at,
        },
    )
    if created:
        HiringEvent.objects.create(
            candidate=candidate, summary="Application added to teacher hiring."
        )
    return candidate


def validate_workflow(candidate: HiringCandidate) -> None:
    errors: dict[str, str] = {}
    if not candidate.is_terminal:
        if not candidate.next_action.strip():
            errors["next_action"] = "Every active candidate needs a next action."
        if not candidate.due_date:
            errors["due_date"] = "Every active candidate needs a due date."
    if (
        candidate.stage in {"hold", "not_selected", "withdrawn"}
        and not candidate.outcome_reason.strip()
    ):
        errors["outcome_reason"] = "Record the reason for this outcome."
    if candidate.stage == "hold":
        if not candidate.review_date or candidate.review_date < timezone.localdate():
            errors["review_date"] = "Choose today or a future date to review this hold."
    allowed = {key for items in CHECKLISTS.values() for key, _ in items}
    if not isinstance(candidate.checklist, list) or any(
        key not in allowed for key in candidate.checklist
    ):
        errors["checklist"] = "Choose valid checklist items."
    if candidate.stage in ACTIVE_STAGES:
        index = ACTIVE_STAGES.index(candidate.stage)
        required = []
        for stage in ACTIVE_STAGES[:index]:
            required += [key for key, _ in CHECKLISTS.get(stage, [])]
        if index >= ACTIVE_STAGES.index("ready"):
            required += [key for key, _ in CHECKLISTS["onboarding"]]
        missing = set(required) - set(candidate.checklist)
        if missing:
            errors["checklist"] = (
                "Complete the earlier-stage checklist items before advancing."
            )
        if (
            index >= ACTIVE_STAGES.index("decision")
            and not candidate.evaluation_notes.strip()
        ):
            errors["evaluation_notes"] = (
                "Record the interview / teaching evaluation before the decision stage."
            )
        if index >= ACTIVE_STAGES.index("offer"):
            if candidate.decision != "hire" or not candidate.decision_notes.strip():
                errors["decision_notes"] = (
                    "Record your decision to hire and its rationale before the offer stage."
                )
            if not candidate.offer_sent_on or not candidate.offer_terms.strip():
                errors["offer_terms"] = (
                    "Record the sent offer date and terms before the offer stage."
                )
        if (
            index >= ACTIVE_STAGES.index("onboarding")
            and candidate.offer_response != "accepted"
        ):
            errors["offer_response"] = (
                "Record the candidate's acceptance before onboarding."
            )
    if candidate.decision != "pending" and not candidate.decision_notes.strip():
        errors["decision_notes"] = "Record the rationale for your hiring decision."
    if candidate.offer_response != "pending" and not candidate.offer_responded_on:
        errors["offer_responded_on"] = "Record when the candidate responded."
    for field in ("offer_sent_on", "offer_responded_on"):
        value = getattr(candidate, field)
        if value and value > timezone.localdate():
            errors[field] = "Use the actual date, not a future date."
    if candidate.offer_responded_on and (
        not candidate.offer_sent_on
        or candidate.offer_responded_on < candidate.offer_sent_on
    ):
        errors["offer_responded_on"] = (
            "The response date must be on or after the offer was sent."
        )
    if errors:
        raise ValidationError(errors)


def select_intake_owner() -> CustomUser | None:
    """Prefer the configured recruiter, then balance work across enabled hiring owners."""
    from django.conf import settings

    eligible = hiring_owner_queryset()
    if settings.RECRUITING_OWNER_EMAIL:
        configured = eligible.filter(
            email__iexact=settings.RECRUITING_OWNER_EMAIL
        ).first()
        if configured:
            return configured
    designated = eligible.filter(hiring_enabled=True)
    if designated.exists():
        return (
            designated.annotate(
                open_candidates=Count(
                    "owned_recruiting_interests__hiring",
                    filter=~Q(
                        owned_recruiting_interests__hiring__stage__in=[
                            "ready",
                            "not_selected",
                            "withdrawn",
                        ]
                    ),
                )
            )
            .order_by("open_candidates", "pk")
            .first()
        )
    return eligible.order_by("pk").first()
