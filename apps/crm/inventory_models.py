import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models


class InventoryChild(models.Model):
    parent = models.ForeignKey(
        "crm.Lead", on_delete=models.CASCADE, related_name="inventory_children"
    )
    name = models.CharField(max_length=120)
    grade = models.CharField(max_length=24)
    home_zip = models.CharField(max_length=10, blank=True)

    @property
    def grade_label(self) -> str:
        from apps.crm.inventory import GRADES

        return GRADES.get(self.grade, {}).get("label", self.grade)

    def __str__(self) -> str:
        return self.name


class InventoryInvitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    child = models.ForeignKey(
        InventoryChild, on_delete=models.CASCADE, related_name="invitations"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    recipient = models.EmailField()
    version = models.CharField(max_length=32, default="parent-inventory-v1")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=0)
    current_group = models.PositiveIntegerField(default=0)
    answers = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering: ClassVar = ["-created_at"]

    @property
    def status(self) -> str:
        if self.revoked_at:
            return "Revoked"
        if self.completed_at:
            return "Completed" if self.reviewed_at else "Completed · Needs review"
        from django.utils import timezone

        if self.expires_at <= timezone.now():
            return "Expired"
        if self.started_at:
            return "Started"
        return "Sent" if self.sent_at else "Awaiting delivery"


class InventoryMail(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        QUEUED = "queued", "Queued for Google delivery"
        SENDING = "sending", "Sending / delivery uncertain"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    invitation = models.ForeignKey(
        InventoryInvitation, on_delete=models.CASCADE, related_name="emails"
    )
    provider_message = models.OneToOneField(
        "crm_email.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_delivery",
    )
    key = models.CharField(max_length=80)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    action_url = models.URLField(max_length=1000, blank=True)
    action_label = models.CharField(max_length=80, blank=True)
    calendar = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["invitation", "key"], name="inventory_mail_once"
            )
        ]
        ordering: ClassVar = ["created_at"]


class ConsultationSlot(models.Model):
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_slots",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    active = models.BooleanField(default=True)

    class Meta:
        ordering: ClassVar = ["starts_at"]
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="inventory_slot_positive",
            )
        ]


class InventoryBooking(models.Model):
    invitation = models.OneToOneField(
        InventoryInvitation, on_delete=models.PROTECT, related_name="booking"
    )
    slot = models.OneToOneField(
        ConsultationSlot, on_delete=models.PROTECT, related_name="booking"
    )
    phone = models.CharField(max_length=32)
    timezone = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class ConsultationBooking(models.Model):
    """A consultation booked from the public calendar page, tied to a CRM contact."""

    lead = models.ForeignKey(
        "crm.Lead", on_delete=models.PROTECT, related_name="consultation_bookings"
    )
    submission = models.OneToOneField(
        "crm.FormSubmission",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consultation_booking",
    )
    slot = models.OneToOneField(
        ConsultationSlot, on_delete=models.PROTECT, related_name="consultation_booking"
    )
    phone = models.CharField(max_length=32)
    timezone = models.CharField(max_length=64)
    child_age_grade = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        return f"Consultation for {self.lead.contact_name} at {self.slot.starts_at:%Y-%m-%d %H:%M}"
