import re

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

    class RelationshipInterest(models.TextChoices):
        REFERRAL_PARTNER = "referral_partner", "Referral Partner"
        DONOR = "donor", "Donor"
        ADVOCATE = "advocate", "Advocate"

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

    @property
    def relationship_interest_labels(self) -> list[str]:
        stored = (self.metadata or {}).get("relationship_interests", [])
        selected = set(stored) if isinstance(stored, list) else set()
        return [label for value, label in self.RelationshipInterest.choices if value in selected]


class Opportunity(TimestampedModel, SoftDeleteModel):
    class Pipeline(models.TextChoices):
        FAMILY_ENROLLMENT = "family_enrollment", "Families / Enrollment"
        REFERRAL_PARTNERS = "referral_partners", "School & Teacher Referral Partners"
        FOUNDATION_DONORS = "foundation_donors", "Foundation Donors"
        FOUNDATION_GRANTS = "foundation_grants", "Foundation Grants / PRIs"
        EQUITY_INVESTMENT = "equity_investment", "Equity / Investment"

    class Stage(models.TextChoices):
        FAMILY_LEAD_NURTURE = "family_lead_nurture", "Lead / Nurture"
        FAMILY_WAITLIST = "family_waitlist", "Waitlist"
        FAMILY_CONSULTATION = "family_consultation", "Consultation Scheduled"
        FAMILY_ASSESSMENT = "family_assessment", "Assessment"
        FAMILY_ENROLLED = "family_enrolled", "Enrolled"
        FAMILY_ACTIVE = "family_active", "Active"
        FAMILY_LOST = "family_lost", "Lost"
        FAMILY_CHURNED = "family_churned", "Churned"
        PARTNER_IDENTIFIED = "partner_identified", "Identified"
        PARTNER_CONTACTED = "partner_contacted", "Contacted"
        PARTNER_MEETING = "partner_meeting", "Meeting / Lunch-and-Learn"
        PARTNER_ACTIVE = "partner_active", "Active Referrer"
        PARTNER_DORMANT = "partner_dormant", "Dormant"
        DONOR_IDENTIFIED = "donor_identified", "Identified"
        DONOR_CULTIVATION = "donor_cultivation", "Cultivation"
        DONOR_ASK = "donor_ask", "Ask"
        DONOR_COMMITTED = "donor_committed", "Committed / Gift"
        DONOR_STEWARDSHIP = "donor_stewardship", "Stewardship"
        DONOR_DECLINED = "donor_declined", "Declined"
        GRANT_NEED_INTRO = "grant_need_intro", "Need Intro"
        GRANT_RELATIONSHIP = "grant_relationship", "Relationship Building"
        GRANT_INVITED = "grant_invited", "LOI / Application Invited"
        GRANT_SUBMITTED = "grant_submitted", "Application Submitted"
        GRANT_AWARDED = "grant_awarded", "Awarded"
        GRANT_DECLINED = "grant_declined", "Declined"
        EQUITY_NEED_INTRO = "equity_need_intro", "Need Intro"
        EQUITY_INTRODUCED = "equity_introduced", "Introduced"
        EQUITY_FIRST_MEETING = "equity_first_meeting", "First Meeting"
        EQUITY_DILIGENCE = "equity_diligence", "Diligence / Data Room"
        EQUITY_TERM_SHEET = "equity_term_sheet", "Term Sheet"
        EQUITY_CLOSED_WON = "equity_closed_won", "Closed-Won"
        EQUITY_PASSED = "equity_passed", "Passed"

    class Priority(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class FundingType(models.TextChoices):
        ESA = "esa", "ESA"
        PRIVATE_PAY = "private_pay", "Private-pay"

    class EsaProgram(models.TextChoices):
        FES_UA = "fes_ua", "FES-UA"
        FES_EO = "fes_eo", "FES-EO"
        PEP = "pep", "PEP"
        FTC = "ftc", "FTC"

    class GradeBand(models.TextChoices):
        PREK_2 = "prek_2", "PreK-2"
        GRADE_3_5 = "3_5", "3-5"
        GRADE_6_8 = "6_8", "6-8"
        MULTI_CHILD = "multi_child", "Multi-child"

    class PartnerType(models.TextChoices):
        PEDIATRIC = "pediatric", "Pediatric practice"
        THERAPY = "therapy", "Therapy clinic"
        PRIVATE_SCHOOL = "private_school", "Private school"
        HOMESCHOOL = "homeschool", "Homeschool co-op / microschool"

    class DonorType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        DAF = "daf", "Donor-advised fund"
        FAMILY_FOUNDATION = "family_foundation", "Family foundation"

    class CapitalLane(models.TextChoices):
        COMPANY = "company", "ClearCode, Inc."
        FOUNDATION = "foundation", "Foundation"
        BOTH = "both", "Both"

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
    name = models.CharField(max_length=255, blank=True)
    pipeline = models.CharField(
        max_length=32,
        choices=Pipeline.choices,
        default=Pipeline.FAMILY_ENROLLMENT,
        db_index=True,
    )
    stage = models.CharField(max_length=32, choices=Stage.choices, default=Stage.FAMILY_LEAD_NURTURE, db_index=True)
    priority = models.CharField(max_length=16, choices=Priority.choices, blank=True, db_index=True)
    student_name = models.CharField(max_length=255, blank=True)
    term_year = models.CharField(max_length=64, blank=True)
    campaign_year = models.CharField(max_length=128, blank=True)
    program_name = models.CharField(max_length=255, blank=True)
    cycle_year = models.PositiveSmallIntegerField(null=True, blank=True)
    investment_round = models.CharField(max_length=128, blank=True)
    funding_type = models.CharField(max_length=16, choices=FundingType.choices, blank=True)
    esa_program = models.CharField(max_length=16, choices=EsaProgram.choices, blank=True)
    grade_band = models.CharField(max_length=16, choices=GradeBand.choices, blank=True)
    in_catchment_zip = models.CharField(max_length=10, blank=True)
    referral_source = models.CharField(max_length=255, blank=True)
    referral_partner = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referred_enrollment_deals",
    )
    partner_type = models.CharField(max_length=32, choices=PartnerType.choices, blank=True)
    donor_type = models.CharField(max_length=32, choices=DonorType.choices, blank=True)
    gift_level = models.CharField(max_length=128, blank=True)
    grant_cycle_application_date = models.DateField(null=True, blank=True)
    capital_lane = models.CharField(max_length=16, choices=CapitalLane.choices, blank=True)
    bucket = models.CharField(max_length=255, blank=True)
    segment_tags = models.CharField(
        max_length=255,
        blank=True,
        help_text="Comma-separated segment properties; never used as pipeline names.",
    )
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
            models.Index(fields=["pipeline", "priority"], name="crm_deal_pipeline_priority"),
            models.Index(fields=["stage", "expected_close_date"]),
            models.Index(fields=["owner", "stage"]),
            models.Index(fields=["company", "pipeline"], name="crm_deal_company_pipeline"),
            models.Index(fields=["school", "stage"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_pipeline_display()})"

    def save(self, *args, **kwargs):
        generated_name = self.convention_name
        if generated_name:
            self.name = generated_name
        super().save(*args, **kwargs)

    @classmethod
    def stage_choices_for_pipeline(cls, pipeline):
        return PIPELINE_STAGE_CHOICES.get(pipeline, ())

    @classmethod
    def stage_values_for_pipeline(cls, pipeline):
        return {value for value, _label in cls.stage_choices_for_pipeline(pipeline)}

    @classmethod
    def initial_stage_for_pipeline(cls, pipeline):
        choices = cls.stage_choices_for_pipeline(pipeline)
        return choices[0][0] if choices else cls.Stage.FAMILY_LEAD_NURTURE

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
        naming_review_pending = bool((self.metadata or {}).get("needs_naming_review"))
        required_fields = {
            self.Pipeline.FAMILY_ENROLLMENT: ("student_name", "term_year"),
            self.Pipeline.REFERRAL_PARTNERS: (),
            self.Pipeline.FOUNDATION_DONORS: ("campaign_year",),
            self.Pipeline.FOUNDATION_GRANTS: ("program_name", "cycle_year"),
            self.Pipeline.EQUITY_INVESTMENT: ("investment_round",),
        }.get(self.pipeline, ())
        if not naming_review_pending:
            for field in required_fields:
                if not getattr(self, field):
                    errors[field] = "Required by this pipeline's naming convention."
            if not self.identity_name:
                errors["lead"] = "A contact or company name is required for this pipeline."
        naming_fields = ("student_name", "term_year", "campaign_year", "program_name", "investment_round")
        if any("(" in str(getattr(self, field) or "") or ")" in str(getattr(self, field) or "") for field in naming_fields):
            errors["name"] = "Create a second deal instead of using parentheses to distinguish work."
        year_pattern = re.compile(r"\b(?:19|20)\d{2}\b")
        if self.pipeline == self.Pipeline.FAMILY_ENROLLMENT and self.term_year and not year_pattern.search(self.term_year):
            errors["term_year"] = "Include a four-digit year in the enrollment name."
        if self.pipeline == self.Pipeline.FOUNDATION_DONORS and self.campaign_year and not year_pattern.search(self.campaign_year):
            errors["campaign_year"] = "Include a four-digit year in the gift name."
        if self.funding_type != self.FundingType.ESA and self.esa_program:
            errors["esa_program"] = "ESA program only applies when funding type is ESA."
        if errors:
            raise ValidationError(errors)

    @property
    def identity_name(self):
        if self.company_id:
            return self.company.name
        if self.lead_id:
            return self.lead.organization_name or self.lead.contact_name or self.lead.school_name
        if self.school_id:
            return self.school.name
        return ""

    @property
    def convention_name(self):
        identity_name = self.identity_name
        if self.pipeline == self.Pipeline.FAMILY_ENROLLMENT and self.student_name and self.term_year:
            return f"{self.student_name} — {self.term_year}"
        if self.pipeline == self.Pipeline.REFERRAL_PARTNERS:
            return identity_name or self.name.strip()
        if self.pipeline == self.Pipeline.FOUNDATION_DONORS and identity_name and self.campaign_year:
            return f"{identity_name} — {self.campaign_year}"
        if self.pipeline == self.Pipeline.FOUNDATION_GRANTS and identity_name and self.program_name and self.cycle_year:
            return f"{identity_name} — {self.program_name} — {self.cycle_year}"
        if self.pipeline == self.Pipeline.EQUITY_INVESTMENT and identity_name and self.investment_round:
            return f"{identity_name} — {self.investment_round}"
        return self.name.strip()

    @property
    def deal_label(self):
        return {
            self.Pipeline.FAMILY_ENROLLMENT: "Enrollment",
            self.Pipeline.REFERRAL_PARTNERS: "Partnership",
            self.Pipeline.FOUNDATION_DONORS: "Gift",
            self.Pipeline.FOUNDATION_GRANTS: "Application",
            self.Pipeline.EQUITY_INVESTMENT: "Investment",
        }.get(self.pipeline, "Deal")

    @property
    def needs_naming_review(self):
        return bool((self.metadata or {}).get("needs_naming_review"))


PIPELINE_STAGE_CHOICES = {
    Opportunity.Pipeline.FAMILY_ENROLLMENT: (
        (Opportunity.Stage.FAMILY_LEAD_NURTURE, "Lead / Nurture"),
        (Opportunity.Stage.FAMILY_WAITLIST, "Waitlist"),
        (Opportunity.Stage.FAMILY_CONSULTATION, "Consultation Scheduled"),
        (Opportunity.Stage.FAMILY_ASSESSMENT, "Assessment"),
        (Opportunity.Stage.FAMILY_ENROLLED, "Enrolled"),
        (Opportunity.Stage.FAMILY_ACTIVE, "Active"),
        (Opportunity.Stage.FAMILY_LOST, "Lost"),
        (Opportunity.Stage.FAMILY_CHURNED, "Churned"),
    ),
    Opportunity.Pipeline.REFERRAL_PARTNERS: (
        (Opportunity.Stage.PARTNER_IDENTIFIED, "Identified"),
        (Opportunity.Stage.PARTNER_CONTACTED, "Contacted"),
        (Opportunity.Stage.PARTNER_MEETING, "Meeting / Lunch-and-Learn"),
        (Opportunity.Stage.PARTNER_ACTIVE, "Active Referrer"),
        (Opportunity.Stage.PARTNER_DORMANT, "Dormant"),
    ),
    Opportunity.Pipeline.FOUNDATION_DONORS: (
        (Opportunity.Stage.DONOR_IDENTIFIED, "Identified"),
        (Opportunity.Stage.DONOR_CULTIVATION, "Cultivation"),
        (Opportunity.Stage.DONOR_ASK, "Ask"),
        (Opportunity.Stage.DONOR_COMMITTED, "Committed / Gift"),
        (Opportunity.Stage.DONOR_STEWARDSHIP, "Stewardship"),
        (Opportunity.Stage.DONOR_DECLINED, "Declined"),
    ),
    Opportunity.Pipeline.FOUNDATION_GRANTS: (
        (Opportunity.Stage.GRANT_NEED_INTRO, "Need Intro"),
        (Opportunity.Stage.GRANT_RELATIONSHIP, "Relationship Building"),
        (Opportunity.Stage.GRANT_INVITED, "LOI / Application Invited"),
        (Opportunity.Stage.GRANT_SUBMITTED, "Application Submitted"),
        (Opportunity.Stage.GRANT_AWARDED, "Awarded"),
        (Opportunity.Stage.GRANT_DECLINED, "Declined"),
    ),
    Opportunity.Pipeline.EQUITY_INVESTMENT: (
        (Opportunity.Stage.EQUITY_NEED_INTRO, "Need Intro"),
        (Opportunity.Stage.EQUITY_INTRODUCED, "Introduced"),
        (Opportunity.Stage.EQUITY_FIRST_MEETING, "First Meeting"),
        (Opportunity.Stage.EQUITY_DILIGENCE, "Diligence / Data Room"),
        (Opportunity.Stage.EQUITY_TERM_SHEET, "Term Sheet"),
        (Opportunity.Stage.EQUITY_CLOSED_WON, "Closed-Won"),
        (Opportunity.Stage.EQUITY_PASSED, "Passed"),
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
        SURVEY = "survey", "Early interest survey"
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
    advocate_selected = models.BooleanField(default=False)
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
