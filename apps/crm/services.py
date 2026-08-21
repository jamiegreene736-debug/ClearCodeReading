from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.crm.models import FormSubmission, Lead
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
    "phone",
    "role_interest",
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
    metadata: dict[str, str] = field(default_factory=dict)


def sanitized_submission_data(post_data):
    """Keep only expected public fields; never persist CSRF or honeypot values."""
    return {
        key: str(post_data.get(key, ""))[:5000]
        for key in PUBLIC_SUBMISSION_FIELDS
        if key in post_data
    }


@transaction.atomic
def record_form_submission(*, intake, form_type, source_path, submitted_data):
    lead = Lead.objects.select_for_update().filter(
        contact_email=intake.contact_email,
        is_deleted=False,
    ).first()
    linked_user = CustomUser.objects.filter(email=intake.contact_email, is_deleted=False).first()
    now = timezone.now()
    metadata = {
        **((lead.metadata if lead else {}) or {}),
        **intake.metadata,
        "latest_signup_at": now.isoformat(),
        "latest_signup_audience": intake.audience,
        "source_path": source_path,
    }

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
