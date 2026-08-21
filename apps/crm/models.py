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
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.school_name} - {self.contact_name}"


class Opportunity(TimestampedModel, SoftDeleteModel):
    class Stage(models.TextChoices):
        DISCOVERY = "discovery", "Discovery"
        DEMO = "demo", "Demo"
        PROPOSAL = "proposal", "Proposal"
        NEGOTIATION = "negotiation", "Negotiation"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True, related_name="opportunities")
    school = models.ForeignKey("schools.School", on_delete=models.SET_NULL, null=True, blank=True, related_name="opportunities")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_opportunities")
    name = models.CharField(max_length=255)
    stage = models.CharField(max_length=32, choices=Stage.choices, default=Stage.DISCOVERY, db_index=True)
    value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    probability = models.PositiveSmallIntegerField(default=0)
    expected_close_date = models.DateField(null=True, blank=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.TextField(blank=True)
    next_steps = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["expected_close_date", "-created_at"]
        indexes = [
            models.Index(fields=["stage", "expected_close_date"]),
            models.Index(fields=["owner", "stage"]),
            models.Index(fields=["school", "stage"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.stage})"


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
