from datetime import timedelta

from django.db import migrations
from django.db.models import Q
from django.utils import timezone


def backfill(apps, schema_editor):
    Application = apps.get_model("core", "RecruitingInterest")
    Candidate = apps.get_model("crm", "HiringCandidate")
    Event = apps.get_model("crm", "HiringEvent")
    User = apps.get_model("users", "CustomUser")
    alias = schema_editor.connection.alias
    applications = Application.objects.using(alias).filter(career_path="teacher")
    # Preserve access for already-appointed CRM recruiting owners, without granting CRM privileges.
    User.objects.using(alias).filter(
        pk__in=applications.values("owner_id"),
        is_active=True,
        is_deleted=False,
    ).filter(
        Q(is_staff=True)
        | Q(is_superuser=True)
        | Q(role__in=["super_admin", "crm_user"])
    ).update(hiring_enabled=True)
    for application in applications.only("pk", "created_at", "status").iterator():
        due = application.created_at.date()
        remaining = 2
        while remaining:
            due += timedelta(days=1)
            if due.weekday() < 5:
                remaining -= 1
        closed = application.status == "closed"
        candidate, created = Candidate.objects.using(alias).get_or_create(
            application_id=application.pk,
            defaults={
                "stage": "hold" if closed else "application",
                "stage_entered_at": application.created_at,
                "next_action": "Review legacy closed outcome"
                if closed
                else "Review application",
                "due_date": timezone.localdate() if closed else due,
                "review_date": timezone.localdate() if closed else None,
                "outcome_reason": "Previously marked Closed; confirm the hiring outcome."
                if closed
                else "",
            },
        )
        if created:
            Candidate.objects.using(alias).filter(pk=candidate.pk).update(
                created_at=application.created_at
            )
            Event.objects.using(alias).create(
                candidate_id=candidate.pk,
                summary="Existing recruiting application added; original owner and documents preserved.",
            )


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0009_hiringcandidate_hiringevent_and_more"),
        ("users", "0006_customuser_hiring_enabled"),
    ]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
