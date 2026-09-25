"""Private calendar settings belonging to an individual consultation host."""

import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models


class HostCalendar(models.Model):
    host = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    google_email = models.EmailField(blank=True)
    encrypted_google_refresh_token = models.TextField(blank=True)
    encrypted_url = models.TextField(blank=True)
    source_timezone = models.CharField(max_length=64, default="America/New_York")
    blocking_timezone = models.CharField(max_length=64, default="America/New_York")
    subscription_token = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)


class BlockingRule(models.Model):
    class Mode(models.TextChoices):
        NONE = "none", "Open to bookings"
        RANGE = "range", "Block these hours"
        ALL = "all", "Block the whole day"

    mode = models.CharField(max_length=5, choices=Mode.choices, default=Mode.NONE)
    starts_at = models.TimeField(null=True, blank=True)
    ends_at = models.TimeField(null=True, blank=True)

    class Meta:
        abstract = True
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        mode="range", starts_at__isnull=False, ends_at__isnull=False
                    )
                    & ~models.Q(starts_at=models.F("ends_at"))
                )
                | models.Q(
                    mode__in=["none", "all"],
                    starts_at__isnull=True,
                    ends_at__isnull=True,
                ),
                name="%(class)s_valid_range",
            ),
        ]


class WeeklyCalendarBlock(BlockingRule):
    calendar = models.ForeignKey(
        HostCalendar, on_delete=models.CASCADE, related_name="weekly_blocks"
    )
    weekday = models.PositiveSmallIntegerField()

    class Meta(BlockingRule.Meta):
        constraints = BlockingRule.Meta.constraints + [
            models.UniqueConstraint(
                fields=["calendar", "weekday"], name="unique_host_weekday_block"
            ),
            models.CheckConstraint(
                condition=models.Q(weekday__lte=6), name="valid_block_weekday"
            ),
        ]
        ordering: ClassVar = ["weekday"]


class CalendarDateOverride(BlockingRule):
    calendar = models.ForeignKey(
        HostCalendar, on_delete=models.CASCADE, related_name="date_overrides"
    )
    date = models.DateField()

    class Meta(BlockingRule.Meta):
        constraints = BlockingRule.Meta.constraints + [
            models.UniqueConstraint(
                fields=["calendar", "date"], name="unique_host_date_override"
            ),
        ]
        ordering: ClassVar = ["date"]


class CalendarAuthorization(models.Model):
    state_hash = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    session_hash = models.CharField(max_length=64)
    encrypted_verifier = models.TextField()
    nonce = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
