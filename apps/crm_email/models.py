import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models


class Mailbox(models.Model):
    class Status(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        CONNECTED = "connected", "Connected"
        RECONNECT = "reconnect", "Reconnect required"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    email = models.EmailField(blank=True)
    google_subject = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DISCONNECTED
    )
    encrypted_refresh_token = models.TextField(blank=True)
    signature = models.TextField(blank=True)
    history_id = models.CharField(max_length=100, blank=True)
    watch_expires_at = models.DateTimeField(null=True, blank=True)
    watch_renewed_at = models.DateTimeField(null=True, blank=True)
    sync_requested_at = models.DateTimeField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    recovery_cursor = models.PositiveBigIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.email or self.user.email


class Authorization(models.Model):
    state_hash = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    session_hash = models.CharField(max_length=64)
    encrypted_verifier = models.TextField()
    nonce = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)


class Conversation(models.Model):
    mailbox = models.ForeignKey(
        Mailbox, on_delete=models.CASCADE, related_name="conversations"
    )
    lead = models.ForeignKey(
        "crm.Lead", on_delete=models.CASCADE, related_name="email_conversations"
    )
    gmail_thread_id = models.CharField(max_length=100)
    subject = models.CharField(max_length=998, blank=True)
    import_pending = models.BooleanField(default=False)
    last_error = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["mailbox", "gmail_thread_id"], name="crm_email_thread_unique"
            )
        ]
        ordering: ClassVar = ["-updated_at"]


class Message(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        UNCERTAIN = "uncertain", "Checking send result"
        SENT = "sent", "Sent"
        RECEIVED = "received", "Received"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey(
        Mailbox, on_delete=models.CASCADE, related_name="messages"
    )
    lead = models.ForeignKey(
        "crm.Lead", on_delete=models.CASCADE, null=True, blank=True
    )
    recruiting_interest = models.ForeignKey(
        "core.RecruitingInterest", on_delete=models.CASCADE, null=True, blank=True
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="messages",
    )
    gmail_id = models.CharField(max_length=100, null=True, blank=True)
    rfc_message_id = models.CharField(max_length=998, blank=True)
    in_reply_to = models.CharField(max_length=998, blank=True)
    references = models.TextField(blank=True)
    reply_to = models.EmailField(blank=True)
    sender = models.EmailField(blank=True)
    to = models.JSONField(default=list)
    cc = models.JSONField(default=list)
    bcc = models.JSONField(default=list)
    subject = models.CharField(max_length=998)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)
    follow_up_days = models.PositiveSmallIntegerField(default=0)
    follow_up = models.OneToOneField(
        "crm.CrmActivity", on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=(
                    models.Q(lead__isnull=False, recruiting_interest__isnull=True)
                    | models.Q(lead__isnull=True, recruiting_interest__isnull=False)
                ),
                name="crm_email_exactly_one_contact",
            ),
            models.UniqueConstraint(
                fields=["mailbox", "gmail_id"], name="crm_email_message_unique"
            ),
        ]
        ordering: ClassVar = ["sent_at", "created_at"]


class Attachment(models.Model):
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="attachments"
    )
    filename = models.CharField(max_length=255)
    size = models.PositiveIntegerField()
    encrypted_data = models.BinaryField()


class EmailTemplate(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    subject = models.CharField(max_length=998)
    body_html = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar = ["name"]


class WorkerHeartbeat(models.Model):
    name = models.CharField(max_length=30, primary_key=True, default="email")
    last_seen_at = models.DateTimeField()


class StageEmailPilot(models.Model):
    """Prelaunch automation is restricted to the approved internal test address."""

    mailbox = models.OneToOneField(Mailbox, on_delete=models.CASCADE)
    equity_mailbox = models.ForeignKey(
        Mailbox,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="equity_pilots",
    )
    equity_signature = models.TextField(blank=True)
    enabled = models.BooleanField(default=False)
    scheduling_link = models.URLField(blank=True, max_length=1000)
    bethany_signature = models.TextField(
        default="Bethany Fleming\nFounder & CEO, ClearCode Reading Center\nbethany@clearcodereading.com"
    )
    foundation_name = models.CharField(default="Bethany Fleming", max_length=150)
    sample_company = models.CharField(default="Example Organization", max_length=255)
    sample_investment_category = models.CharField(blank=True, max_length=255)
    updated_at = models.DateTimeField(auto_now=True)


class StageEmailDelivery(models.Model):
    deal = models.OneToOneField("crm.Opportunity", on_delete=models.CASCADE)
    pilot = models.ForeignKey(StageEmailPilot, on_delete=models.PROTECT)
    pipeline = models.CharField(max_length=32)
    message = models.OneToOneField(
        Message,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stage_delivery",
    )
    error = models.CharField(max_length=255, blank=True)
    cancelled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
