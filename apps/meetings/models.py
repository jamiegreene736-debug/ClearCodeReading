from typing import ClassVar

from django.db import models
from django.db.models import F, Q

from apps.users.models import ChildProfile, CustomUser


class FamilyMeeting(models.Model):
    teacher = models.ForeignKey(CustomUser, on_delete=models.PROTECT)
    parent = models.ForeignKey(
        CustomUser, related_name="invited_family_meetings", on_delete=models.PROTECT
    )
    child = models.ForeignKey(ChildProfile, on_delete=models.PROTECT)
    school = models.ForeignKey(
        "schools.School", null=True, blank=True, on_delete=models.PROTECT
    )
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    location = models.CharField(max_length=400)
    revision = models.PositiveIntegerField(default=1)
    cancelled = models.BooleanField(default=False)

    class Meta:
        ordering: ClassVar = ["starts_at", "pk"]
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")),
                name="family_meeting_positive_duration",
            ),
        ]


class MeetingNotice(models.Model):
    class Kind(models.TextChoices):
        INVITE = "invite", "Invitation"
        UPDATE = "update", "Updated meeting"
        CANCEL = "cancel", "Cancellation"
        REMINDER = "reminder", "Reminder"

    class State(models.TextChoices):
        PENDING = "pending", "Queued"
        CHECKING = "checking", "Checking delivery"
        SENT = "sent", "Accepted by Gmail"
        SKIPPED = "skipped", "Superseded or ineligible"
        REVIEW = "review", "Needs delivery review"

    meeting = models.ForeignKey(
        FamilyMeeting, on_delete=models.CASCADE, related_name="notices"
    )
    revision = models.PositiveIntegerField()
    kind = models.CharField(max_length=12, choices=Kind.choices)
    state = models.CharField(
        max_length=12, choices=State.choices, default=State.PENDING
    )
    due_at = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)
    sent_at = models.DateTimeField(null=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["meeting", "revision", "kind"],
                name="one_family_notice_per_revision",
            ),
        ]
        indexes: ClassVar = [models.Index(fields=["state", "due_at"])]
