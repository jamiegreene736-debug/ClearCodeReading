from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.sessions.models import AuditedModel, SoftDeleteModel, TimestampedModel

if TYPE_CHECKING:
    from apps.users.models import CustomUser


class Flag(AuditedModel):
    """Explainable instructional-review signal raised from recorded evidence."""

    class Code(models.TextChoices):
        THREE_RETEACH_SESSIONS = "three_reteach_sessions", "Three re-teach sessions"
        FLAT_ACCURACY = "flat_accuracy", "Flat accuracy"
        MASTERY_TIME_OUTLIER = "mastery_time_outlier", "Mastery-time outlier"
        REGRESSION_AFTER_MASTERY = "regression_after_mastery", "Regression after mastery"
        ERROR_PATTERN_PERSISTENT = "error_pattern_persistent", "Persistent error pattern"
        ATTENDANCE_INTERRUPTION = "attendance_interruption", "Attendance interruption"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    child = models.ForeignKey(
        "users.ChildProfile",
        on_delete=models.PROTECT,
        related_name="instructional_flags",
    )
    code = models.CharField(max_length=40, choices=Code.choices, db_index=True)
    trigger_rule = models.JSONField(
        default=dict,
        help_text="Versioned rule inputs and thresholds that explain why the flag was raised.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    raised_at = models.DateTimeField(default=timezone.now, db_index=True)
    evidence_snapshot = models.JSONField(
        default=dict,
        help_text="Immutable instructional evidence available when the flag was raised.",
    )
    related_session = models.ForeignKey(
        "intervention_sessions.Session",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="instructional_flags",
    )
    curriculum_position = models.ForeignKey(
        "curriculum.CurriculumSequence",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="instructional_flags",
    )
    routed_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="routed_instructional_flags",
    )
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_instructional_flags",
    )
    model_or_rule_version = models.CharField(max_length=80)

    class Meta:
        ordering = ["-raised_at", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(related_session__isnull=False) | Q(curriculum_position__isnull=False),
                name="flag_has_session_or_position",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "status", "raised_at"]),
            models.Index(fields=["center", "code", "status"]),
            models.Index(fields=["child", "status", "raised_at"]),
            models.Index(fields=["related_session", "code"]),
            models.Index(fields=["curriculum_position", "status"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.child_id and self.child.school_id != self.center_id:
            errors["center"] = "Flag and child must belong to the same center."
        if self.related_session_id:
            if self.related_session.center_id != self.center_id:
                errors["related_session"] = "Related session must belong to the flag center."
            if self.related_session.child_id != self.child_id:
                errors["related_session"] = "Related session must belong to the flag child."
        if self.curriculum_position_id and self.curriculum_position.center_id != self.center_id:
            errors["curriculum_position"] = "Curriculum position must belong to the flag center."
        for field_name in ("routed_to", "acknowledged_by"):
            user = getattr(self, field_name)
            if user and not _user_belongs_to_center(user, self.center_id):
                errors[field_name] = "User must belong to the flag center."
        if self.status == self.Status.ACKNOWLEDGED and not self.acknowledged_by_id:
            errors["acknowledged_by"] = "Acknowledged flags require the acknowledging staff member."
        if not isinstance(self.trigger_rule, dict):
            errors["trigger_rule"] = "Trigger rule must be a structured object."
        if not isinstance(self.evidence_snapshot, dict):
            errors["evidence_snapshot"] = "Evidence snapshot must be a structured object."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.get_code_display()} for {self.child}"


class Milestone(AuditedModel):
    """A child-centered instructional milestone defined before V2 engine work."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In progress"
        ACHIEVED = "achieved", "Achieved"
        REVISED = "revised", "Revised"

    child = models.ForeignKey(
        "users.ChildProfile",
        on_delete=models.PROTECT,
        related_name="instructional_milestones",
    )
    definition = models.TextField()
    curriculum_position = models.ForeignKey(
        "curriculum.CurriculumSequence",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="instructional_milestones",
    )
    skill_band = models.CharField(max_length=80, blank=True)
    target_date = models.DateField(null=True, blank=True, db_index=True)
    achieved_date = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED, db_index=True)

    class Meta:
        ordering = ["target_date", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(curriculum_position__isnull=False) & Q(skill_band=""))
                    | (Q(curriculum_position__isnull=True) & ~Q(skill_band=""))
                ),
                name="milestone_has_position_xor_skill_band",
            ),
            models.CheckConstraint(
                condition=Q(target_date__isnull=False) | Q(achieved_date__isnull=False),
                name="milestone_has_target_or_achieved_date",
            ),
            models.CheckConstraint(
                condition=~Q(status="achieved") | Q(achieved_date__isnull=False),
                name="achieved_milestone_has_date",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "status", "target_date"]),
            models.Index(fields=["child", "status", "target_date"]),
            models.Index(fields=["curriculum_position", "status"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.child_id and self.child.school_id != self.center_id:
            errors["center"] = "Milestone and child must belong to the same center."
        if self.curriculum_position_id and self.curriculum_position.center_id != self.center_id:
            errors["curriculum_position"] = "Curriculum position must belong to the milestone center."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.child}: {self.definition}"


class Prediction(AuditedModel):
    """Versioned, explainable estimate for instructional planning."""

    DEFAULT_DISCLAIMER = (
        "This estimate supports instructional planning and is not a diagnosis or a guarantee."
    )

    child = models.ForeignKey(
        "users.ChildProfile",
        on_delete=models.PROTECT,
        related_name="instructional_predictions",
    )
    target_milestone = models.ForeignKey(
        Milestone,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="predictions",
    )
    target_position = models.ForeignKey(
        "curriculum.CurriculumSequence",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="instructional_predictions",
    )
    estimated_sessions = models.PositiveIntegerField(null=True, blank=True)
    estimated_date = models.DateField(null=True, blank=True)
    confidence = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    model_version = models.CharField(max_length=80)
    evidence = models.JSONField(
        default=dict,
        help_text="Structured input references and evidence used to produce the estimate.",
    )
    generated_at = models.DateTimeField(default=timezone.now, db_index=True)
    disclaimer = models.TextField(default=DEFAULT_DISCLAIMER)

    class Meta:
        ordering = ["-generated_at", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(target_milestone__isnull=False) & Q(target_position__isnull=True))
                    | (Q(target_milestone__isnull=True) & Q(target_position__isnull=False))
                ),
                name="prediction_has_one_target",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(estimated_sessions__isnull=False) & Q(estimated_date__isnull=True))
                    | (Q(estimated_sessions__isnull=True) & Q(estimated_date__isnull=False))
                ),
                name="prediction_has_one_estimate",
            ),
            models.CheckConstraint(
                condition=Q(confidence__gte=0) & Q(confidence__lte=1),
                name="prediction_confidence_between_0_and_1",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "generated_at"]),
            models.Index(fields=["child", "generated_at"]),
            models.Index(fields=["target_position", "generated_at"]),
            models.Index(fields=["target_milestone", "generated_at"]),
            models.Index(fields=["model_version", "generated_at"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.child_id and self.child.school_id != self.center_id:
            errors["center"] = "Prediction and child must belong to the same center."
        if self.target_milestone_id:
            if self.target_milestone.center_id != self.center_id:
                errors["target_milestone"] = "Target milestone must belong to the prediction center."
            if self.target_milestone.child_id != self.child_id:
                errors["target_milestone"] = "Target milestone must belong to the prediction child."
        if self.target_position_id and self.target_position.center_id != self.center_id:
            errors["target_position"] = "Target position must belong to the prediction center."
        if not isinstance(self.evidence, dict):
            errors["evidence"] = "Prediction evidence must be a structured object."
        if not self.disclaimer.strip():
            errors["disclaimer"] = "Prediction disclaimer is required."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Prediction for {self.child} generated {self.generated_at:%Y-%m-%d}"


class OutcomeAggregate(TimestampedModel, SoftDeleteModel):
    """De-identified cohort metric with no child or staff relationship."""

    MINIMUM_COHORT_SIZE = 5

    class Dimension(models.TextChoices):
        METHODOLOGY = "methodology", "Methodology"
        GRADE_BAND = "grade_band", "Grade band"
        CENTER = "center", "Center"
        PERIOD = "period", "Period"

    center = models.ForeignKey(
        "schools.School",
        on_delete=models.PROTECT,
        related_name="outcome_aggregates",
    )
    dimension = models.CharField(max_length=24, choices=Dimension.choices, db_index=True)
    dimension_value = models.CharField(
        max_length=120,
        help_text="Aggregate cohort label only; never store a child or staff identifier.",
    )
    metric_name = models.CharField(max_length=120, db_index=True)
    value = models.DecimalField(max_digits=18, decimal_places=4)
    cohort_size = models.PositiveIntegerField(validators=[MinValueValidator(MINIMUM_COHORT_SIZE)])
    period_start = models.DateField(db_index=True)
    period_end = models.DateField(db_index=True)
    generated_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-period_end", "center", "dimension", "metric_name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(cohort_size__gte=5),
                name="outcome_cohort_meets_privacy_floor",
            ),
            models.CheckConstraint(
                condition=Q(period_end__gte=models.F("period_start")),
                name="outcome_period_end_on_or_after_start",
            ),
            models.UniqueConstraint(
                fields=[
                    "center",
                    "dimension",
                    "dimension_value",
                    "metric_name",
                    "period_start",
                    "period_end",
                ],
                name="unique_outcome_metric_cohort_period",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "period_start", "period_end"]),
            models.Index(fields=["center", "dimension", "metric_name"]),
            models.Index(fields=["dimension", "dimension_value", "period_end"]),
            models.Index(fields=["metric_name", "period_end"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.period_start and self.period_end and self.period_end < self.period_start:
            errors["period_end"] = "Period end must be on or after period start."
        if not self.dimension_value.strip():
            errors["dimension_value"] = "Aggregate dimension value is required."
        if not self.metric_name.strip():
            errors["metric_name"] = "Aggregate metric name is required."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.center}: {self.metric_name} by {self.dimension} "
            f"({self.period_start:%Y-%m-%d} to {self.period_end:%Y-%m-%d})"
        )


def _user_belongs_to_center(user: "CustomUser", center_id: int) -> bool:
    if user.is_superuser or getattr(user, "role", None) == "super_admin":
        return True
    return user.school_memberships.filter(
        school_id=center_id,
        is_deleted=False,
    ).exists()
