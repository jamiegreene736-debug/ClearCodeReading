from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.crm.models import FormSubmission, IntakeTriage, Lead, Opportunity
from apps.users.models import AuditLog, CustomUser


PUBLIC_SUBMISSION_FIELDS = {
    "audience",
    "career_path",
    "child_age_grade",
    "consent",
    "email",
    "estimated_students",
    "name",
    "notes",
    "organization_name",
    "partner_interest",
    "relationship_interests",
    "phone",
    "role_interest",
    "support_topic",
}


@dataclass(frozen=True)
class LeadIntake:
    contact_email: str
    contact_name: str
    school_name: str
    audience: str
    organization_name: str = ""
    contact_phone: str = ""
    estimated_students: int | None = None
    notes: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


def sanitized_submission_data(post_data) -> dict[str, str | list[str]]:
    """Keep only expected public fields; never persist CSRF or honeypot values."""
    sanitized = {}
    for key in PUBLIC_SUBMISSION_FIELDS:
        if key not in post_data:
            continue
        if key == "relationship_interests" and hasattr(post_data, "getlist"):
            sanitized[key] = normalize_relationship_interests(post_data.getlist(key))
        else:
            sanitized[key] = str(post_data.get(key, ""))[:5000]
    return sanitized


def normalize_relationship_interests(values) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    allowed = set(Lead.RelationshipInterest.values)
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if str(value).strip() in allowed
        )
    )


@transaction.atomic
def record_form_submission(*, intake, form_type, source_path, submitted_data):
    lead = Lead.objects.select_for_update().filter(
        contact_email=intake.contact_email,
        is_deleted=False,
    ).first()
    linked_user = CustomUser.objects.filter(email=intake.contact_email, is_deleted=False).first()
    now = timezone.now()
    previous_interests = normalize_relationship_interests(
        ((lead.metadata if lead else {}) or {}).get("relationship_interests", [])
    )
    submitted_interests = normalize_relationship_interests(
        intake.metadata.get("relationship_interests", [])
    )
    metadata = {
        **((lead.metadata if lead else {}) or {}),
        **intake.metadata,
        "latest_signup_at": now.isoformat(),
        "latest_signup_audience": intake.audience,
        "source_path": source_path,
    }
    combined_interests = list(dict.fromkeys([*previous_interests, *submitted_interests]))
    if combined_interests:
        metadata["relationship_interests"] = combined_interests

    if lead is None:
        lead = Lead.objects.create(
            contact_email=intake.contact_email,
            contact_name=intake.contact_name,
            school_name=intake.school_name,
            audience=intake.audience,
            organization_name=intake.organization_name,
            contact_phone=intake.contact_phone,
            estimated_students=intake.estimated_students,
            notes=intake.notes,
            source=Lead.Source.WEBSITE,
            linked_user=linked_user,
            metadata=metadata,
        )
        AuditLog.objects.create(
            actor=linked_user,
            action="lead.created_from_website",
            entity_type="Lead",
            entity_id=str(lead.id),
            after={"email": intake.contact_email, "audience": intake.audience},
        )
    else:
        lead.contact_name = intake.contact_name or lead.contact_name
        lead.school_name = intake.school_name or lead.school_name
        lead.audience = intake.audience or lead.audience
        lead.organization_name = intake.organization_name or lead.organization_name
        lead.contact_phone = intake.contact_phone or lead.contact_phone
        lead.estimated_students = (
            intake.estimated_students
            if intake.estimated_students is not None
            else lead.estimated_students
        )
        lead.notes = intake.notes or lead.notes
        lead.linked_user = linked_user or lead.linked_user
        lead.metadata = metadata
        if lead.status == Lead.Status.UNQUALIFIED:
            lead.status = Lead.Status.NEW
        lead.save()

    submission = FormSubmission.objects.create(
        lead=lead,
        form_type=form_type,
        source_path=source_path[:255],
        submitted_data=submitted_data,
    )
    return lead, submission


TERMINAL_DEAL_STAGES = {
    Opportunity.Stage.FAMILY_ENROLLED,
    Opportunity.Stage.FAMILY_ACTIVE,
    Opportunity.Stage.FAMILY_LOST,
    Opportunity.Stage.FAMILY_CHURNED,
    Opportunity.Stage.PARTNER_ACTIVE,
    Opportunity.Stage.PARTNER_DORMANT,
    Opportunity.Stage.DONOR_COMMITTED,
    Opportunity.Stage.DONOR_STEWARDSHIP,
    Opportunity.Stage.DONOR_DECLINED,
    Opportunity.Stage.GRANT_AWARDED,
    Opportunity.Stage.GRANT_DECLINED,
    Opportunity.Stage.EQUITY_CLOSED_WON,
    Opportunity.Stage.EQUITY_PASSED,
}


def partner_interest_is_selected(value):
    return str(value).strip().lower() in {"1", "on", "true", "yes"}


@transaction.atomic
def ensure_family_enrollment_deal(*, lead, owner=None):
    existing = (
        Opportunity.objects.select_for_update()
        .filter(
            lead=lead,
            pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
            is_deleted=False,
        )
        .exclude(stage__in=TERMINAL_DEAL_STAGES)
        .first()
    )
    if existing:
        return existing, False
    deal = Opportunity(
        lead=lead,
        company=lead.company,
        owner=owner or lead.assigned_to,
        name=f"{lead.contact_name} — Enrollment",
        pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
        stage=Opportunity.initial_stage_for_pipeline(Opportunity.Pipeline.FAMILY_ENROLLMENT),
        probability=10,
        metadata={
            "created_from_family_intake": True,
            "needs_naming_review": True,
        },
    )
    deal.full_clean()
    deal.save()
    return deal, True


def create_partner_triage(*, lead, submission):
    triage, _created = IntakeTriage.objects.get_or_create(
        submission=submission,
        defaults={
            "lead": lead,
            "source_signal": IntakeTriage.SourceSignal.PARTNER_INTEREST,
        },
    )
    return triage


@transaction.atomic
def resolve_triage_item(*, triage, pipelines, actor, notes="", dismiss=False, advocate=False):
    triage = IntakeTriage.objects.select_for_update().select_related("lead").get(pk=triage.pk)
    if triage.status != IntakeTriage.Status.PENDING:
        return triage

    selected = list(dict.fromkeys(pipelines))
    invalid = set(selected) - set(Opportunity.Pipeline.values)
    if invalid:
        raise ValueError("One or more selected pipelines are invalid.")
    if not dismiss and not selected and not advocate:
        raise ValueError("Choose at least one destination pipeline, choose Advocate, or dismiss the triage item.")

    created_deals = []
    if not dismiss:
        for pipeline in selected:
            relationship_filter = (
                {"company": triage.lead.company}
                if triage.lead.company_id
                else {"lead": triage.lead, "company__isnull": True}
            )
            deal = (
                Opportunity.objects.select_for_update()
                .filter(
                    pipeline=pipeline,
                    is_deleted=False,
                    **relationship_filter,
                )
                .exclude(stage__in=TERMINAL_DEAL_STAGES)
                .first()
            )
            if deal is None:
                deal = Opportunity(
                    lead=triage.lead,
                    company=triage.lead.company,
                    owner=triage.lead.assigned_to or actor,
                    name=f"{triage.lead.contact_name} — {Opportunity.Pipeline(pipeline).label}",
                    pipeline=pipeline,
                    stage=Opportunity.initial_stage_for_pipeline(pipeline),
                    probability=10,
                    metadata={
                        "created_from_triage_id": triage.pk,
                        "needs_naming_review": True,
                    },
                )
                deal.full_clean()
                deal.save()
            created_deals.append(deal)

        for deal in created_deals:
            deal.related_deals.add(*(other for other in created_deals if other.pk != deal.pk))

    triage.status = IntakeTriage.Status.DISMISSED if dismiss else IntakeTriage.Status.RESOLVED
    triage.selected_pipelines = [] if dismiss else selected
    triage.advocate_selected = False if dismiss else advocate
    triage.resolution_notes = notes.strip()
    triage.resolved_by = actor
    triage.resolved_at = timezone.now()
    triage.full_clean()
    triage.save(
        update_fields=[
            "status",
            "selected_pipelines",
            "advocate_selected",
            "resolution_notes",
            "resolved_by",
            "resolved_at",
            "updated_at",
        ]
    )
    triage.created_deals.add(*created_deals)
    return triage
