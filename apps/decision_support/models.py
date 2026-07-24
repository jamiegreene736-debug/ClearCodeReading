from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditedModel(TimestampedModel):
    center = models.ForeignKey(
        "schools.School",
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_records",
    )
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_%(app_label)s_%(class)s_records",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_%(app_label)s_%(class)s_records",
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            previous_revision = type(self).objects.filter(pk=self.pk).values_list("revision", flat=True).first()
            if previous_revision is not None:
                self.revision = previous_revision + 1
                if kwargs.get("update_fields") is not None:
                    kwargs["update_fields"] = set(kwargs["update_fields"]) | {"revision"}
        super().save(*args, **kwargs)


class GrowthFlag(AuditedModel):
    class Code(models.TextChoices):
        THREE_RETEACH_SESSIONS = "three_reteach_sessions", "Three consecutive re-teach sessions"
        FLAT_ACCURACY = "flat_accuracy", "Flat accuracy"
        MASTERY_TIME_OUTLIER = "mastery_time_outlier", "Mastery-time review"
        REGRESSION_AFTER_MASTERY = "regression_after_mastery", "Retention review after mastery"
        ERROR_PATTERN_PERSISTENT = "error_pattern_persistent", "Persistent error pattern"
        ATTENDANCE_INTERRUPTION = "attendance_interruption", "Attendance interruption"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    child = models.ForeignKey("users.ChildProfile", on_delete=models.PROTECT, related_name="growth_flags")
    trigger_session = models.ForeignKey(
        "intervention_sessions.Session",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_growth_flags",
    )
    position = models.ForeignKey(
        "curriculum.CurriculumSequence",
        on_delete=models.PROTECT,
        related_name="growth_flags",
    )
    flag_code = models.CharField(max_length=40, choices=Code.choices, db_index=True)
    severity = models.CharField(max_length=12, choices=Severity.choices, db_index=True)
    evidence_snapshot = models.JSONField(default=dict)
    explanation = models.TextField()
    advisory_recommendation = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    routed_to = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="routed_growth_flags",
    )
    opened_at = models.DateTimeField(default=timezone.now, db_index=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_growth_flags",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_growth_flags",
    )
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-opened_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["child", "position", "flag_code"],
                condition=Q(status="open"),
                name="unique_open_growth_flag",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "status", "severity"]),
            models.Index(fields=["child", "status", "opened_at"]),
            models.Index(fields=["position", "flag_code", "status"]),
        ]

    def __str__(self):
        return f"{self.child}: {self.get_flag_code_display()}"

    def clean(self):
        super().clean()
        errors = {}
        if self.child_id and self.child.school_id and self.center_id != self.child.school_id:
            errors["center"] = "Growth flag must use the child's center."
        if self.position_id and self.center_id != self.position.center_id:
            errors["position"] = "Growth flag position must belong to the selected center."
        if self.trigger_session_id:
            if self.trigger_session.center_id != self.center_id:
                errors["trigger_session"] = "Trigger session must belong to the selected center."
            elif self.trigger_session.child_id != self.child_id:
                errors["trigger_session"] = "Trigger session must belong to the selected child."
            elif self.trigger_session.curriculum_position_id != self.position_id:
                errors["trigger_session"] = "Trigger session must use the flagged position."
        if errors:
            raise ValidationError(errors)


class MilestonePrediction(AuditedModel):
    class Confidence(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    child = models.ForeignKey("users.ChildProfile", on_delete=models.PROTECT, related_name="milestone_predictions")
    placement = models.ForeignKey(
        "curriculum.StudentPlacement",
        on_delete=models.PROTECT,
        related_name="milestone_predictions",
    )
    target_position = models.ForeignKey(
        "curriculum.CurriculumSequence",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="milestone_predictions",
    )
    target_label = models.CharField(max_length=255)
    predicted_sessions = models.PositiveIntegerField()
    predicted_date = models.DateField()
    lower_bound_sessions = models.PositiveIntegerField()
    upper_bound_sessions = models.PositiveIntegerField()
    confidence = models.CharField(max_length=12, choices=Confidence.choices, db_index=True)
    evidence_summary = models.JSONField(default=dict)
    explanation = models.TextField()
    parent_timeline = models.TextField()
    disclaimer = models.TextField(
        default=(
            "This is an instructional planning estimate based on recent progress and attendance. "
            "It may change and is not a guarantee."
        )
    )
    engine_version = models.CharField(max_length=40, default="deterministic-2026.1")
    generated_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_current = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-generated_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["child"],
                condition=Q(is_current=True),
                name="unique_current_milestone_prediction",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "is_current", "generated_at"]),
            models.Index(fields=["child", "is_current"]),
            models.Index(fields=["target_position", "predicted_date"]),
        ]

    def __str__(self):
        return f"{self.child}: {self.target_label} around {self.predicted_date}"

    def clean(self):
        super().clean()
        errors = {}
        if self.child_id and self.child.school_id and self.center_id != self.child.school_id:
            errors["center"] = "Milestone prediction must use the child's center."
        if self.placement_id:
            if self.placement.center_id != self.center_id:
                errors["placement"] = "Placement must belong to the selected center."
            elif self.placement.child_id != self.child_id:
                errors["placement"] = "Placement must belong to the selected child."
        if self.target_position_id and self.placement_id:
            if self.target_position.curriculum_id != self.placement.curriculum_id:
                errors["target_position"] = "Target position must belong to the placement curriculum."
        if errors:
            raise ValidationError(errors)

    def parent_payload(self):
        return {
            "status": "prediction",
            "label": self.target_label,
            "current_position": self.placement.current_position.code,
            "target_position": self.target_position.code if self.target_position else None,
            "predicted_sessions": self.predicted_sessions,
            "estimated_date": self.predicted_date,
            "confidence": self.confidence,
            "confidence_band_sessions": {
                "lower": self.lower_bound_sessions,
                "upper": self.upper_bound_sessions,
            },
            "plain_language_timeline": self.parent_timeline,
            "evidence_summary": self.evidence_summary,
            "generated_at": self.generated_at,
            "disclaimer": self.disclaimer,
        }
