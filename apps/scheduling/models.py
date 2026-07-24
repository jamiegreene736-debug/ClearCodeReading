from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ProviderAvailability(TimestampedModel):
    center = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="provider_availability")
    specialist = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="provider_availability")
    windows = models.JSONField(default=list, blank=True)
    max_group_size = models.PositiveSmallIntegerField(default=4)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["center", "specialist"], name="unique_center_provider_availability")]

    def clean(self):
        if self.center_id and self.specialist_id and not self.specialist.school_memberships.filter(
            school_id=self.center_id,
            role__in=["owner", "admin", "specialist"],
            is_deleted=False,
        ).exists() and not self.specialist.is_superuser:
            raise ValidationError({"specialist": "Provider must belong to this center."})


class ScheduleGroupProposal(TimestampedModel):
    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    center = models.ForeignKey("schools.School", on_delete=models.PROTECT, related_name="schedule_group_proposals")
    specialist = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="schedule_group_proposals",
    )
    curriculum = models.ForeignKey(
        "curriculum.Curriculum",
        on_delete=models.PROTECT,
        related_name="schedule_group_proposals",
    )
    children = models.ManyToManyField("users.ChildProfile", related_name="schedule_group_proposals")
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    score = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROPOSED, db_index=True)
    rationale = models.TextField(blank=True)
    signature = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_schedule_group_proposals",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_schedule_group_proposals",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-score", "starts_at", "specialist_id"]
        constraints = [
            models.UniqueConstraint(fields=["center", "signature"], name="unique_center_schedule_proposal_signature"),
        ]
        indexes = [
            models.Index(fields=["center", "status", "starts_at"]),
            models.Index(fields=["specialist", "starts_at"]),
        ]

    def clean(self):
        errors = {}
        if self.curriculum_id and self.center_id != self.curriculum.center_id:
            errors["curriculum"] = "Proposal and curriculum must belong to the same center."
        if self.specialist_id and self.center_id and not self.specialist.school_memberships.filter(
            school_id=self.center_id,
            is_deleted=False,
        ).exists() and not self.specialist.is_superuser:
            errors["specialist"] = "Specialist must belong to this center."
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = "End time must be after start time."
        if errors:
            raise ValidationError(errors)


class ScheduleBooking(TimestampedModel):
    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELED = "canceled", "Canceled"

    class SyncStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SYNCED = "synced", "Synced"
        ERROR = "error", "Error"

    center = models.ForeignKey("schools.School", on_delete=models.PROTECT, related_name="schedule_bookings")
    proposal = models.ForeignKey(
        ScheduleGroupProposal,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="bookings",
    )
    child = models.ForeignKey("users.ChildProfile", on_delete=models.PROTECT, related_name="schedule_bookings")
    specialist = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="schedule_bookings")
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROPOSED, db_index=True)
    scheduler_provider = models.CharField(max_length=32, blank=True)
    external_booking_id = models.CharField(max_length=160, blank=True, db_index=True)
    sync_status = models.CharField(max_length=20, choices=SyncStatus.choices, default=SyncStatus.PENDING)
    sync_error = models.TextField(blank=True)
    sync_attempts = models.PositiveIntegerField(default=0)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_schedule_bookings")
    approved_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["center", "starts_at"]), models.Index(fields=["specialist", "starts_at"])]

    def clean(self):
        errors = {}
        if self.child_id and self.center_id != self.child.school_id:
            errors["center"] = "Booking must use the child's center."
        if self.child_id and not self.child.idea_services_authorized:
            errors["child"] = "IEP-aligned scheduling requires recorded authorization."
        if self.specialist_id and self.center_id and not self.specialist.school_memberships.filter(
            school_id=self.center_id,
            is_deleted=False,
        ).exists() and not self.specialist.is_superuser:
            errors["specialist"] = "Specialist must belong to this center."
        if self.proposal_id and self.center_id != self.proposal.center_id:
            errors["proposal"] = "Booking and proposal must belong to the same center."
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = "End time must be after start time."
        if errors:
            raise ValidationError(errors)


class WaitlistEntry(TimestampedModel):
    center = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="waitlist_entries")
    child = models.ForeignKey("users.ChildProfile", on_delete=models.CASCADE, related_name="waitlist_entries")
    submarket = models.CharField(max_length=120, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["center", "child"], condition=models.Q(is_active=True), name="unique_active_center_waitlist")]

    def clean(self):
        if self.child_id and self.center_id != self.child.school_id:
            raise ValidationError({"center": "Waitlist entry must use the child's center."})
