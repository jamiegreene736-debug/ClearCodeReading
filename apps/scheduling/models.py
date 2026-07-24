from django.conf import settings
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
    child = models.ForeignKey("users.ChildProfile", on_delete=models.PROTECT, related_name="schedule_bookings")
    specialist = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="schedule_bookings")
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROPOSED, db_index=True)
    scheduler_provider = models.CharField(max_length=32, blank=True)
    external_booking_id = models.CharField(max_length=160, blank=True, db_index=True)
    sync_status = models.CharField(max_length=20, choices=SyncStatus.choices, default=SyncStatus.PENDING)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_schedule_bookings")
    approved_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["center", "starts_at"]), models.Index(fields=["specialist", "starts_at"])]


class WaitlistEntry(TimestampedModel):
    center = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="waitlist_entries")
    child = models.ForeignKey("users.ChildProfile", on_delete=models.CASCADE, related_name="waitlist_entries")
    submarket = models.CharField(max_length=120, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["center", "child"], condition=models.Q(is_active=True), name="unique_active_center_waitlist")]
