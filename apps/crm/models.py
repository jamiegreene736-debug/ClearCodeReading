from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])


class Company(TimestampedModel, SoftDeleteModel):
    name = models.CharField(max_length=255, db_index=True)
    website = models.URLField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_crm_companies",
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name", "id"]
        verbose_name_plural = "companies"
        indexes = [
            models.Index(fields=["owner", "name"], name="crm_company_owner_name"),
            models.Index(fields=["is_deleted", "name"], name="crm_company_active_name"),
        ]

    def __str__(self):
        return self.name


class Lead(TimestampedModel, SoftDeleteModel):
    class Audience(models.TextChoices):
        PARENT = "parent", "Parent"
        TEACHER = "teacher", "Teacher"
        SCHOOL = "school", "School or District"
        OTHER = "other", "Other"

    class Source(models.TextChoices):
        WEBSITE = "website", "Website"
        REFERRAL = "referral", "Referral"
        CONFERENCE = "conference", "Conference"
        OUTBOUND = "outbound", "Outbound"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        QUALIFIED = "qualified", "Qualified"
        UNQUALIFIED = "unqualified", "Unqualified"
        CONVERTED = "converted", "Converted"

    school_name = models.CharField(max_length=255)
    contact_name = models.CharField(max_length=255)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=32, blank=True)
    audience = models.CharField(max_length=32, choices=Audience.choices, default=Audience.PARENT, db_index=True)
    organization_name = models.CharField(max_length=255, blank=True)
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contacts",
    )
    source = models.CharField(max_length=32, choices=Source.choices, default=Source.WEBSITE, db_index=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.NEW, db_index=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_leads")
    linked_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_leads")
    estimated_students = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["contact_email"]),
            models.Index(fields=["audience", "status"]),
            models.Index(fields=["source", "status"]),
            models.Index(fields=["linked_user", "status"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["company", "status"], name="crm_lead_company_status"),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.school_name} - {self.contact_name}"


class Opportunity(TimestampedModel, SoftDeleteModel):
    class Pipeline(models.TextChoices):
        FAMILY_ENROLLMENT = "family_enrollment", "Families / Enrollment"
        REFERRAL_PARTNERS = "referral_partners", "Referral Partners"
        FOUNDATION_DONORS = "foundation_donors", "Foundation Donors"
        FOUNDATION_GRANTS = "foundation_grants", "Foundation Grants / PRIs"
        EQUITY_INVESTMENT = "equity_investment", "Equity / Investment"

    class Stage(models.TextChoices):
        NEW = "new", "New inquiry"
        CONSULTATION = "consultation", "Consultation scheduled"
        QUALIFIED = "qualified", "Qualified"
        ENROLLMENT_OFFERED = "enrollment_offered", "Enrollment offered"
        ENROLLED = "enrolled", "Enrolled"
        IDENTIFIED = "identified", "Identified"
        CONTACTED = "contacted", "Contacted"
        ACTIVE_PARTNER = "active_partner", "Active partner"
        INACTIVE = "inactive", "Inactive"
        CULTIVATING = "cultivating", "Cultivating"
        ASK_PLANNED = "ask_planned", "Ask planned"
        ASK_MADE = "ask_made", "Ask made"
        PLEDGED = "pledged", "Pledged"
        GIFT_RECEIVED = "gift_received", "Gift received"
        STEWARDSHIP = "stewardship", "Stewardship"
        LOI = "loi", "LOI"
        APPLICATION = "application", "Application"
        SUBMITTED = "submitted", "Submitted"
        DUE_DILIGENCE = "due_diligence", "Due diligence"
        AWARDED = "awarded", "Awarded"
        REPORTING_RENEWAL = "reporting_renewal", "Reporting / renewal"
        TERMS = "terms", "Terms"
        COMMITTED = "committed", "Committed"
        FUNDED = "funded", "Funded"
        LOST = "lost", "Closed lost"
        DECLINED = "declined", "Declined"
        PASSED = "passed", "Passed"

    lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True, related_name="opportunities")
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deals",
    )
    school = models.ForeignKey("schools.School", on_delete=models.SET_NULL, null=True, blank=True, related_name="opportunities")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_opportunities")
    name = models.CharField(max_length=255)
    pipeline = models.CharField(
        max_length=32,
        choices=Pipeline.choices,
        default=Pipeline.FAMILY_ENROLLMENT,
        db_index=True,
    )
    stage = models.CharField(max_length=32, choices=Stage.choices, default=Stage.NEW, db_index=True)
    value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    probability = models.PositiveSmallIntegerField(default=0)
    expected_close_date = models.DateField(null=True, blank=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.TextField(blank=True)
    next_steps = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    related_deals = models.ManyToManyField("self", blank=True)

    class Meta:
        ordering = ["expected_close_date", "-created_at"]
        verbose_name = "deal"
        verbose_name_plural = "deals"
        indexes = [
            models.Index(fields=["pipeline", "stage"], name="crm_deal_pipeline_stage"),
            models.Index(fields=["stage", "expected_close_date"]),
            models.Index(fields=["owner", "stage"]),
            models.Index(fields=["company", "pipeline"], name="crm_deal_company_pipeline"),
            models.Index(fields=["school", "stage"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_pipeline_display()})"

    @classmethod
    def stage_choices_for_pipeline(cls, pipeline):
        return PIPELINE_STAGE_CHOICES.get(pipeline, ())

    @classmethod
    def stage_values_for_pipeline(cls, pipeline):
        return {value for value, _label in cls.stage_choices_for_pipeline(pipeline)}

    @classmethod
    def initial_stage_for_pipeline(cls, pipeline):
        choices = cls.stage_choices_for_pipeline(pipeline)
        return choices[0][0] if choices else cls.Stage.NEW

    def clean(self):
        super().clean()
        errors = {}
        if self.pipeline not in self.Pipeline.values:
            errors["pipeline"] = "Choose a valid CRM pipeline."
        elif self.stage not in self.stage_values_for_pipeline(self.pipeline):
            errors["stage"] = "Choose a stage that belongs to this deal's pipeline."
        if self.probability > 100:
            errors["probability"] = "Probability must be between 0 and 100."
        if self.lead_id and self.company_id and self.lead.company_id not in {None, self.company_id}:
            errors["company"] = "The deal company must match the contact's company."
        if not self.lead_id and not self.company_id and not self.school_id:
            errors["lead"] = "Associate the deal with a contact or company."
        if errors:
            raise ValidationError(errors)


PIPELINE_STAGE_CHOICES = {
    Opportunity.Pipeline.FAMILY_ENROLLMENT: (
        (Opportunity.Stage.NEW, "New inquiry"),
        (Opportunity.Stage.CONSULTATION, "Consultation scheduled"),
        (Opportunity.Stage.QUALIFIED, "Qualified"),
        (Opportunity.Stage.ENROLLMENT_OFFERED, "Enrollment offered"),
        (Opportunity.Stage.ENROLLED, "Enrolled"),
        (Opportunity.Stage.LOST, "Closed lost"),
    ),
    Opportunity.Pipeline.REFERRAL_PARTNERS: (
        (Opportunity.Stage.IDENTIFIED, "Identified"),
        (Opportunity.Stage.CONTACTED, "Contacted"),
        (Opportunity.Stage.QUALIFIED, "Qualified"),
        (Opportunity.Stage.ACTIVE_PARTNER, "Active partner"),
        (Opportunity.Stage.INACTIVE, "Inactive"),
        (Opportunity.Stage.LOST, "Closed lost"),
    ),
    Opportunity.Pipeline.FOUNDATION_DONORS: (
        (Opportunity.Stage.IDENTIFIED, "Identified"),
        (Opportunity.Stage.CULTIVATING, "Cultivating"),
        (Opportunity.Stage.ASK_PLANNED, "Ask planned"),
        (Opportunity.Stage.ASK_MADE, "Ask made"),
        (Opportunity.Stage.PLEDGED, "Pledged"),
        (Opportunity.Stage.GIFT_RECEIVED, "Gift received"),
        (Opportunity.Stage.STEWARDSHIP, "Stewardship"),
        (Opportunity.Stage.LOST, "Closed lost"),
    ),
    Opportunity.Pipeline.FOUNDATION_GRANTS: (
        (Opportunity.Stage.QUALIFIED, "Qualified"),
        (Opportunity.Stage.LOI, "LOI"),
        (Opportunity.Stage.APPLICATION, "Application"),
        (Opportunity.Stage.SUBMITTED, "Submitted"),
        (Opportunity.Stage.DUE_DILIGENCE, "Due diligence"),
        (Opportunity.Stage.AWARDED, "Awarded"),
        (Opportunity.Stage.REPORTING_RENEWAL, "Reporting / renewal"),
        (Opportunity.Stage.DECLINED, "Declined"),
    ),
    Opportunity.Pipeline.EQUITY_INVESTMENT: (
        (Opportunity.Stage.IDENTIFIED, "Identified"),
        (Opportunity.Stage.CONTACTED, "Introduced / contacted"),
        (Opportunity.Stage.QUALIFIED, "Qualified"),
        (Opportunity.Stage.DUE_DILIGENCE, "Due diligence"),
        (Opportunity.Stage.TERMS, "Terms"),
        (Opportunity.Stage.COMMITTED, "Committed"),
        (Opportunity.Stage.FUNDED, "Funded"),
        (Opportunity.Stage.PASSED, "Passed"),
    ),
}


class NewsletterSubscription(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        UNSUBSCRIBED = "unsubscribed", "Unsubscribed"

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    consented_at = models.DateTimeField(default=timezone.now)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)
    source_path = models.CharField(max_length=255, blank=True)
    consent_version = models.CharField(max_length=32, default="newsletter-v1")
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="crm_newssub_status_created"),
        ]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class NewsletterCampaign(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        PARTIALLY_FAILED = "partially_failed", "Partially failed"

    subject = models.CharField(max_length=255)
    preview_text = models.CharField(max_length=255, blank=True)
    body = models.TextField(help_text="Plain text; paragraph breaks are preserved in the HTML email.")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="newsletter_campaigns_created",
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="newsletter_campaigns_sent",
    )
    sending_started_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    recipient_count = models.PositiveIntegerField(default=0)
    delivered_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="crm_newscam_status_created"),
        ]

    def clean(self):
        super().clean()
        if "\n" in self.subject or "\r" in self.subject:
            raise ValidationError({"subject": "The subject cannot contain line breaks."})

    def __str__(self):
        return self.subject


class NewsletterDelivery(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped after unsubscribe"

    campaign = models.ForeignKey(NewsletterCampaign, on_delete=models.CASCADE, related_name="deliveries")
    subscription = models.ForeignKey(
        NewsletterSubscription,
        on_delete=models.PROTECT,
        related_name="deliveries",
    )
    recipient_email = models.EmailField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["campaign_id", "recipient_email"]
        verbose_name_plural = "newsletter deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "subscription"],
                name="unique_newsletter_campaign_subscription",
            ),
        ]
        indexes = [
            models.Index(fields=["campaign", "status"], name="crm_newsdel_campaign_status"),
        ]

    def __str__(self):
        return f"{self.campaign}: {self.recipient_email} ({self.status})"


class FormSubmission(TimestampedModel):
    """Immutable intake evidence for a valid public form submission."""

    class FormType(models.TextChoices):
        CONSULTATION = "consultation", "Consultation request"
        ASSESSMENT = "assessment", "Assessment follow-up"
        CAREER = "career", "Career interest"
        NEWSLETTER = "newsletter", "Newsletter signup"
        WEBSITE = "website", "Website inquiry"

    lead = models.ForeignKey(
        Lead,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="form_submissions",
    )
    form_type = models.CharField(max_length=32, choices=FormType.choices, db_index=True)
    source_path = models.CharField(max_length=255, blank=True, db_index=True)
    submitted_data = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["form_type", "created_at"], name="crm_form_type_created"),
            models.Index(fields=["lead", "created_at"], name="crm_form_lead_created"),
        ]

    def __str__(self):
        return f"{self.get_form_type_display()} at {self.created_at:%Y-%m-%d %H:%M}"


class IntakeTriage(TimestampedModel):
    class SourceSignal(models.TextChoices):
        PARTNER_INTEREST = "partner_interest", "Family partner interest"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="triage_items")
    submission = models.OneToOneField(
        FormSubmission,
        on_delete=models.CASCADE,
        related_name="triage_item",
    )
    source_signal = models.CharField(max_length=32, choices=SourceSignal.choices, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    selected_pipelines = models.JSONField(default=list, blank=True)
    resolution_notes = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_crm_triage_items",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_deals = models.ManyToManyField(Opportunity, blank=True, related_name="source_triage_items")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="crm_triage_status_created"),
            models.Index(fields=["source_signal", "status"], name="crm_triage_signal_status"),
        ]

    def clean(self):
        super().clean()
        invalid_pipelines = set(self.selected_pipelines) - set(Opportunity.Pipeline.values)
        if invalid_pipelines:
            raise ValidationError({"selected_pipelines": "Choose only valid CRM pipelines."})

    def __str__(self):
        return f"{self.get_source_signal_display()} — {self.lead.contact_name}"


class CrmActivity(TimestampedModel):
    class ActivityType(models.TextChoices):
        NOTE = "note", "Note"
        TASK = "task", "Task"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="crm_activities")
    activity_type = models.CharField(max_length=16, choices=ActivityType.choices, db_index=True)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="crm_activities_created",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="crm_tasks_assigned",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "CRM activities"
        indexes = [
            models.Index(fields=["lead", "activity_type", "created_at"], name="crm_activity_lead_type"),
            models.Index(fields=["activity_type", "completed_at", "due_at"], name="crm_activity_task_state"),
        ]

    def clean(self):
        super().clean()
        if self.activity_type == self.ActivityType.NOTE and not self.body.strip():
            raise ValidationError({"body": "A note cannot be empty."})
        if self.activity_type == self.ActivityType.TASK and not self.subject.strip():
            raise ValidationError({"subject": "A task needs a title."})

    def __str__(self):
        return self.subject or f"{self.get_activity_type_display()} for {self.lead.contact_name}"
