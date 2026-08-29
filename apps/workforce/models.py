from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PayerLegalEntity(TimestampedModel):
    legal_name = models.CharField(max_length=255, unique=True)
    display_name = models.CharField(max_length=255)
    jurisdiction_state = models.CharField(max_length=2, default="FL")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name_plural = "payer legal entities"

    def __str__(self) -> str:
        return self.display_name


class WorkforceRoleMembership(TimestampedModel):
    class Role(models.TextChoices):
        WORKFORCE_ADMIN = "workforce_admin", "Workforce administrator"
        COMPLIANCE_REVIEWER = "compliance_reviewer", "Compliance reviewer"
        FINANCE_PREPARER = "finance_preparer", "Finance preparer"
        FINANCE_APPROVER = "finance_approver", "Finance approver"

    payer = models.ForeignKey(PayerLegalEntity, on_delete=models.CASCADE, related_name="role_memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workforce_roles")
    role = models.CharField(max_length=32, choices=Role.choices, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["payer", "user", "role"], name="unique_workforce_role"),
        ]
        indexes = [models.Index(fields=["user", "role", "is_active"])]

    def __str__(self) -> str:
        return f"{self.user} - {self.get_role_display()}"


class WorkerProfile(TimestampedModel):
    class Status(models.TextChoices):
        CANDIDATE = "candidate", "Candidate"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="worker_profile")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CANDIDATE, db_index=True)

    def __str__(self) -> str:
        return str(self.user)


class Engagement(TimestampedModel):
    class Classification(models.TextChoices):
        PENDING = "pending", "Pending review"
        CONTRACTOR = "contractor", "Independent contractor"
        EMPLOYEE = "employee", "Employee"

    class Status(models.TextChoices):
        CANDIDATE = "candidate", "Candidate"
        CLASSIFICATION_PENDING = "classification_pending", "Classification pending"
        ONBOARDING = "onboarding", "Onboarding"
        READY = "ready", "Ready to pay"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        ENDED = "ended", "Ended"

    class DeliveryContext(models.TextChoices):
        VIRTUAL = "virtual", "Virtual"
        CLEARCODE_SITE = "clearcode_site", "ClearCode site"
        SCHOOL_SITE = "school_site", "School site"
        MIXED = "mixed", "Mixed"

    payer = models.ForeignKey(PayerLegalEntity, on_delete=models.PROTECT, related_name="engagements")
    worker = models.ForeignKey(WorkerProfile, on_delete=models.PROTECT, related_name="engagements")
    classification = models.CharField(
        max_length=16,
        choices=Classification.choices,
        default=Classification.PENDING,
        db_index=True,
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.CLASSIFICATION_PENDING,
        db_index=True,
    )
    work_state = models.CharField(max_length=2, default="FL", db_index=True)
    delivery_context = models.CharField(
        max_length=24,
        choices=DeliveryContext.choices,
        default=DeliveryContext.VIRTUAL,
    )
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    contract_signed_on = models.DateField(null=True, blank=True)
    first_reportable_payment_on = models.DateField(null=True, blank=True)
    anticipated_calendar_year_compensation = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Operational estimate used only to determine whether Florida reporting is expected.",
    )

    class Meta:
        indexes = [
            models.Index(fields=["payer", "classification", "status"]),
            models.Index(fields=["worker", "status"]),
            models.Index(fields=["work_state", "status"]),
        ]

    def clean(self) -> None:
        if self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "End date cannot be before the start date."})
        if self.anticipated_calendar_year_compensation is not None and self.anticipated_calendar_year_compensation < 0:
            raise ValidationError({"anticipated_calendar_year_compensation": "Anticipated compensation cannot be negative."})

    def __str__(self) -> str:
        return f"{self.worker} - {self.get_classification_display()}"


class WorkerAssignment(TimestampedModel):
    engagement = models.ForeignKey(Engagement, on_delete=models.CASCADE, related_name="assignments")
    center = models.ForeignKey("schools.School", on_delete=models.PROTECT, related_name="workforce_assignments")
    is_active = models.BooleanField(default=True, db_index=True)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["engagement", "center", "starts_on"], name="unique_worker_assignment_start"),
        ]
        indexes = [models.Index(fields=["center", "is_active"])]

    def clean(self) -> None:
        errors: dict[str, str] = {}
        if self.ends_on and self.ends_on < self.starts_on:
            errors["ends_on"] = "End date cannot be before the start date."
        if self.engagement_id and self.starts_on < self.engagement.starts_on:
            errors["starts_on"] = "Assignment cannot start before the engagement."
        if errors:
            raise ValidationError(errors)


class ClassificationReview(TimestampedModel):
    class Decision(models.TextChoices):
        CONTRACTOR = "contractor", "Independent contractor"
        EMPLOYEE = "employee", "Employee"
        NEEDS_REVIEW = "needs_review", "Needs further review"

    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="classification_reviews")
    version = models.PositiveIntegerField(editable=False)
    decision = models.CharField(max_length=16, choices=Decision.choices, db_index=True)
    rationale = models.TextField()
    evidence = models.JSONField(default=dict, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="workforce_classification_reviews",
    )
    reviewed_at = models.DateTimeField(default=timezone.now, db_index=True)
    next_review_due = models.DateField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(fields=["engagement", "version"], name="unique_classification_review_version"),
        ]


class SensitiveDataReference(TimestampedModel):
    class Custodian(models.TextChoices):
        EXTERNAL_PROVIDER = "external_provider", "External provider"
        INTERNAL_VAULT = "internal_vault", "Internal restricted vault"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REQUIRES_ACTION = "requires_action", "Requires action"
        REVOKED = "revoked", "Revoked"

    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="sensitive_data_references")
    custodian = models.CharField(max_length=24, choices=Custodian.choices, default=Custodian.EXTERNAL_PROVIDER)
    provider = models.CharField(max_length=64)
    external_subject_id = models.CharField(max_length=255)
    data_categories = models.JSONField(default=list)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "external_subject_id"], name="unique_sensitive_provider_subject"),
        ]


class ProviderOnboarding(TimestampedModel):
    class Status(models.TextChoices):
        NOT_INVITED = "not_invited", "Not invited"
        INVITED = "invited", "Invited"
        IN_PROGRESS = "in_progress", "In progress"
        REQUIRES_ACTION = "requires_action", "Requires action"
        READY = "ready", "Ready"
        DISABLED = "disabled", "Disabled"

    engagement = models.OneToOneField(Engagement, on_delete=models.PROTECT, related_name="provider_onboarding")
    provider = models.CharField(max_length=64)
    external_onboarding_id = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.NOT_INVITED, db_index=True)
    invite_expires_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    remediation_codes = models.JSONField(default=list, blank=True)


class Agreement(TimestampedModel):
    class Kind(models.TextChoices):
        CONTRACTOR = "contractor", "Independent contractor agreement"
        EMPLOYMENT = "employment", "Employment agreement"
        CONFIDENTIALITY = "confidentiality", "Confidentiality agreement"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SIGNED = "signed", "Signed"
        EXPIRED = "expired", "Expired"
        TERMINATED = "terminated", "Terminated"

    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="agreements")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    effective_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    external_document_id = models.CharField(max_length=255, blank=True)

    def __str__(self) -> str:
        return f"{self.get_kind_display()} — {self.engagement.worker}"


class Credential(TimestampedModel):
    class Kind(models.TextChoices):
        BACKGROUND_SCREENING = "background_screening", "Background screening"
        PROFESSIONAL_LICENSE = "professional_license", "Professional license"
        TRAINING = "training", "Required training"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CLEARED = "cleared", "Cleared"
        REQUIRES_ACTION = "requires_action", "Requires action"
        EXPIRED = "expired", "Expired"
        NOT_REQUIRED = "not_required", "Not required"

    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="credentials")
    center = models.ForeignKey(
        "schools.School",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="workforce_credentials",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING, db_index=True)
    expires_on = models.DateField(null=True, blank=True, db_index=True)
    external_reference = models.CharField(max_length=255, blank=True)


class RateSchedule(TimestampedModel):
    class Unit(models.TextChoices):
        SESSION = "session", "Session"
        HOUR = "hour", "Hour"
        FIXED = "fixed", "Fixed amount"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        RETIRED = "retired", "Retired"

    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="rates")
    center = models.ForeignKey(
        "schools.School",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="workforce_rates",
    )
    unit = models.CharField(max_length=16, choices=Unit.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="workforce_rates_created")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="workforce_rates_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["engagement", "center", "status", "starts_on"])]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="workforce_rate_positive")]

    def clean(self) -> None:
        errors: dict[str, str] = {}
        if self.ends_on and self.ends_on < self.starts_on:
            errors["ends_on"] = "End date cannot be before the start date."
        if self.approved_by_id and self.approved_by_id == self.created_by_id:
            errors["approved_by"] = "Rate creator and approver must be different people."
        if errors:
            raise ValidationError(errors)


class ComplianceTask(TimestampedModel):
    class Kind(models.TextChoices):
        FL_NEW_HIRE_REPORT = "fl_new_hire_report", "Florida independent-contractor report"
        FEDERAL_1099 = "federal_1099", "Federal Form 1099"
        W9_VERIFICATION = "w9_verification", "Form W-9 verification"
        BACKGROUND_SCREENING = "background_screening", "Background screening review"
        E_VERIFY = "e_verify", "E-Verify review"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        WAIVED = "waived", "Waived"
        BLOCKED = "blocked", "Blocked"

    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="compliance_tasks")
    kind = models.CharField(max_length=32, choices=Kind.choices, db_index=True)
    tax_year = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    trigger_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)
    external_reference = models.CharField(max_length=255, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="workforce_compliance_tasks_completed",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["engagement", "kind", "tax_year"], name="unique_engagement_compliance_year"),
        ]


class TaxYearSummary(TimestampedModel):
    class Status(models.TextChoices):
        TRACKING = "tracking", "Tracking"
        READY_TO_FILE = "ready_to_file", "Ready to file"
        FILED = "filed", "Filed"
        CORRECTED = "corrected", "Corrected"
        NOT_REQUIRED = "not_required", "Not required"

    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="tax_year_summaries")
    tax_year = models.PositiveSmallIntegerField()
    total_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    filing_threshold = models.DecimalField(max_digits=12, decimal_places=2)
    filing_required = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TRACKING, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["engagement", "tax_year"], name="unique_engagement_tax_year"),
        ]


class PayableItem(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        IN_RUN = "in_run", "In payment run"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="payables")
    center = models.ForeignKey("schools.School", on_delete=models.PROTECT, related_name="workforce_payables")
    source_session = models.OneToOneField(
        "intervention_sessions.Session",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payable_item",
    )
    service_date = models.DateField(db_index=True)
    description = models.CharField(max_length=255)
    units = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("1.00"))
    rate = models.ForeignKey(RateSchedule, on_delete=models.PROTECT, related_name="payables")
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="workforce_payables_created")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="workforce_payables_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["center", "status", "service_date"])]
        constraints = [
            models.CheckConstraint(condition=Q(units__gt=0), name="payable_units_positive"),
            models.CheckConstraint(condition=Q(gross_amount__gt=0), name="payable_gross_positive"),
        ]

    def clean(self) -> None:
        errors: dict[str, str] = {}
        if self.rate_id and self.rate.engagement_id != self.engagement_id:
            errors["rate"] = "Rate must belong to this engagement."
        if self.rate_id and self.rate.center_id not in (None, self.center_id):
            errors["rate"] = "Rate does not apply to this center."
        if self.source_session_id:
            if self.source_session.center_id != self.center_id:
                errors["source_session"] = "Session and payable must use the same center."
            if self.source_session.specialist_id != self.engagement.worker.user_id:
                errors["source_session"] = "Session specialist must be the engaged worker."
        if self.approved_by_id and self.approved_by_id == self.created_by_id:
            errors["approved_by"] = "Payable submitter and approver must be different people."
        if errors:
            raise ValidationError(errors)


class PaymentRun(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEWED = "reviewed", "Reviewed"
        APPROVED = "approved", "Approved"
        SUBMITTING = "submitting", "Submitting"
        SUBMITTED = "submitted", "Submitted"
        SETTLED = "settled", "Settled"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    payer = models.ForeignKey(PayerLegalEntity, on_delete=models.PROTECT, related_name="payment_runs")
    period_start = models.DateField()
    period_end = models.DateField()
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payment_runs_created")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_runs_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_runs_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    external_batch_id = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [models.Index(fields=["payer", "status", "period_end"])]

    def clean(self) -> None:
        errors: dict[str, str] = {}
        if self.period_end < self.period_start:
            errors["period_end"] = "Period end cannot be before period start."
        actors = [actor for actor in [self.created_by_id, self.reviewed_by_id, self.approved_by_id] if actor]
        if len(actors) != len(set(actors)):
            errors["approved_by"] = "Creator, reviewer, and approver must be different people."
        if errors:
            raise ValidationError(errors)

    @property
    def total_amount(self) -> Decimal:
        return sum((payment.amount for payment in self.payments.all()), Decimal("0.00"))


class Payment(TimestampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SUBMITTED = "submitted", "Submitted"
        SETTLED = "settled", "Settled"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    payment_run = models.ForeignKey(PaymentRun, on_delete=models.PROTECT, related_name="payments")
    payable = models.OneToOneField(PayableItem, on_delete=models.PROTECT, related_name="payment")
    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    external_payment_id = models.CharField(max_length=255, blank=True)
    failure_code = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive")]

    def clean(self) -> None:
        if self.payable_id and self.engagement_id != self.payable.engagement_id:
            raise ValidationError({"engagement": "Payment and payable must use the same engagement."})


class ProviderEvent(TimestampedModel):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PROCESSED = "processed", "Processed"
        REJECTED = "rejected", "Rejected"

    provider = models.CharField(max_length=64)
    external_event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=120)
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECEIVED, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "external_event_id"], name="unique_workforce_provider_event"),
        ]
