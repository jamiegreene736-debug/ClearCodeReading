"""Hiring workflow attached to the original, private recruiting application."""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.crm.models import TimestampedModel


class HiringCandidate(TimestampedModel):
    selection_url: str = ""

    class Stage(models.TextChoices):
        APPLICATION = "application", "Application received"
        SCREENING = "screening", "Initial screening"
        INTERVIEW = "interview", "Interview / teaching demonstration"
        DECISION = "decision", "Hiring decision"
        OFFER = "offer", "Offer sent"
        ONBOARDING = "onboarding", "Onboarding"
        READY = "ready", "Ready for assignment"
        HOLD = "hold", "On hold"
        NOT_SELECTED = "not_selected", "Not selected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    class Decision(models.TextChoices):
        PENDING = "pending", "Not yet decided"
        HIRE = "hire", "Proceed with hire"
        DECLINE = "decline", "Do not proceed"

    class OfferResponse(models.TextChoices):
        PENDING = "pending", "Awaiting response"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    application = models.OneToOneField(
        "core.RecruitingInterest", on_delete=models.PROTECT, related_name="hiring"
    )
    lead = models.OneToOneField(
        "crm.Lead",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hiring",
    )
    stage = models.CharField(
        max_length=24, choices=Stage.choices, default=Stage.APPLICATION, db_index=True
    )
    stage_entered_at = models.DateTimeField(default=timezone.now)
    next_action = models.CharField(max_length=255, default="Review application")
    due_date = models.DateField(null=True, blank=True, db_index=True)
    blocker = models.TextField(blank=True)
    outcome_reason = models.TextField(blank=True)
    review_date = models.DateField(null=True, blank=True)
    checklist = models.JSONField(default=list, blank=True)
    evaluation_notes = models.TextField(blank=True)
    decision = models.CharField(
        max_length=16, choices=Decision.choices, default=Decision.PENDING
    )
    decision_notes = models.TextField(blank=True)
    offer_sent_on = models.DateField(null=True, blank=True)
    offer_terms = models.TextField(blank=True)
    offer_response = models.CharField(
        max_length=16, choices=OfferResponse.choices, default=OfferResponse.PENDING
    )
    offer_responded_on = models.DateField(null=True, blank=True)
    onboarding_notes = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["due_date", "created_at", "pk"]
        indexes = [
            models.Index(fields=["stage", "due_date"], name="crm_hiring_stage_due")
        ]

    def clean(self) -> None:
        from apps.crm.hiring import validate_workflow

        super().clean()
        validate_workflow(self)

    @property
    def is_terminal(self) -> bool:
        return self.stage in {
            self.Stage.READY,
            self.Stage.NOT_SELECTED,
            self.Stage.WITHDRAWN,
        }

    @property
    def is_overdue(self) -> bool:
        return bool(
            not self.is_terminal
            and self.due_date
            and self.due_date < timezone.localdate()
        )

    @property
    def days_in_stage(self) -> int:
        return max(
            0, (timezone.localdate() - timezone.localdate(self.stage_entered_at)).days
        )

    def __str__(self) -> str:
        return self.application.name


class HiringEvent(models.Model):
    candidate = models.ForeignKey(
        HiringCandidate, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(default=timezone.now)
    summary = models.CharField(max_length=500)
    changes = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    @property
    def changed_field_labels(self) -> list[str]:
        labels = {
            field.name: str(field.verbose_name).capitalize()
            for field in HiringCandidate._meta.fields
        }
        return [
            labels[field] for field in self.changes.get("fields", []) if field in labels
        ]
