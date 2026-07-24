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


class Group(TimestampedModel):
    """A center-scoped instructional group using one exact curriculum."""

    center = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="instructional_groups")
    name = models.CharField(max_length=160)
    curriculum = models.ForeignKey(
        "curriculum.Curriculum",
        on_delete=models.PROTECT,
        related_name="instructional_groups",
    )
    skill_band = models.CharField(max_length=120, blank=True)
    sequence_start = models.ForeignKey(
        "curriculum.CurriculumSequence",
        on_delete=models.PROTECT,
        related_name="groups_starting_here",
    )
    sequence_end = models.ForeignKey(
        "curriculum.CurriculumSequence",
        on_delete=models.PROTECT,
        related_name="groups_ending_here",
    )
    students = models.ManyToManyField(
        "users.ChildProfile",
        through="GroupMembership",
        related_name="instructional_groups",
        blank=True,
    )
    primary_specialist = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="primary_instructional_groups",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["center__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["center", "name"],
                condition=models.Q(is_active=True),
                name="unique_active_group_name_per_center",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "curriculum", "is_active"]),
            models.Index(fields=["primary_specialist", "is_active"]),
            models.Index(fields=["sequence_start", "sequence_end"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.curriculum_id and self.curriculum.center_id != self.center_id:
            errors["curriculum"] = "Group curriculum must belong to the group center."
        for field_name in ("sequence_start", "sequence_end"):
            position = getattr(self, field_name, None)
            if position and position.curriculum_id != self.curriculum_id:
                errors[field_name] = "Sequence range positions must use the group curriculum."
        if self.sequence_start_id and self.sequence_end_id:
            if self.sequence_start.sequence_order > self.sequence_end.sequence_order:
                errors["sequence_end"] = "Sequence range end must not precede its start."
        if self.primary_specialist_id and self.center_id:
            is_center_specialist = self.primary_specialist.is_superuser or self.primary_specialist.school_memberships.filter(
                school_id=self.center_id,
                is_deleted=False,
            ).exists()
            if not is_center_specialist:
                errors["primary_specialist"] = "Primary specialist must belong to the group center."
        if errors:
            raise ValidationError(errors)

    @property
    def methodology(self):
        return self.curriculum.code

    def __str__(self):
        return f"{self.name} ({self.curriculum.get_code_display()})"


class GroupMembership(TimestampedModel):
    """Validated membership connecting a child to a methodology-safe group."""

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="memberships")
    child = models.ForeignKey(
        "users.ChildProfile",
        on_delete=models.CASCADE,
        related_name="instructional_group_memberships",
    )

    class Meta:
        ordering = ["group", "child__last_name", "child__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["group", "child"], name="unique_instructional_group_child"),
        ]
        indexes = [models.Index(fields=["group", "child"])]

    def clean(self):
        super().clean()
        errors = {}
        if self.group_id and self.child_id:
            if self.child.school_id != self.group.center_id:
                errors["child"] = "Student and group must belong to the same center."
            active_placement = self.child.curriculum_placements.filter(
                is_active=True,
                is_deleted=False,
            ).select_related("curriculum").first()
            if active_placement is None:
                errors["child"] = "Student needs an active placement before joining a group."
            elif active_placement.curriculum_id != self.group.curriculum_id:
                errors["child"] = "Student placement must use the group's exact curriculum and methodology."
            elif not (
                self.group.sequence_start.sequence_order
                <= active_placement.current_position.sequence_order
                <= self.group.sequence_end.sequence_order
            ):
                errors["child"] = "Student placement must be within the group's sequence range."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.child} in {self.group}"


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
