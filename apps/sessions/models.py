from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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


class AuditedModel(TimestampedModel, SoftDeleteModel):
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


class Session(AuditedModel):
    """Single source of truth for specialist instructional capture (FR-2.1)."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELED = "canceled", "Canceled"

    class InterventionPart(models.TextChoices):
        PFR_1A = "pfr_1a", "PFR Session 1a"
        PFR_1B = "pfr_1b", "PFR Session 1b"
        OG_CONCEPT = "og_concept", "OG+ Concept Session"

    child = models.ForeignKey(
        "users.ChildProfile",
        on_delete=models.PROTECT,
        related_name="intervention_sessions",
    )
    specialist = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="specialist_sessions",
    )
    curriculum_position = models.ForeignKey(
        "curriculum.CurriculumSequence",
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    targeted_positions = models.ManyToManyField(
        "curriculum.CurriculumSequence",
        blank=True,
        related_name="targeted_in_sessions",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED, db_index=True)
    intervention_part = models.CharField(max_length=20, choices=InterventionPart.choices, db_index=True)
    scheduled_start = models.DateTimeField(db_index=True)
    started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    activities_completed = models.JSONField(default=list, blank=True)
    item_sets = models.JSONField(default=dict, blank=True)
    accuracy_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    accuracy_numerator = models.PositiveIntegerField(null=True, blank=True)
    accuracy_denominator = models.PositiveIntegerField(null=True, blank=True)
    time_to_mastery_signals = models.JSONField(default=dict, blank=True)
    error_patterns = models.JSONField(default=list, blank=True)
    behavioral_observations = models.JSONField(default=list, blank=True)
    next_session_direction = models.TextField(blank=True)
    home_practice_suggestion = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-scheduled_start", "-created_at"]
        indexes = [
            models.Index(fields=["center", "scheduled_start"]),
            models.Index(fields=["child", "scheduled_start"]),
            models.Index(fields=["specialist", "scheduled_start"]),
            models.Index(fields=["curriculum_position", "status"]),
            models.Index(fields=["intervention_part", "status"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        json_types = {
            "activities_completed": (self.activities_completed, list),
            "item_sets": (self.item_sets, dict),
            "time_to_mastery_signals": (self.time_to_mastery_signals, dict),
            "error_patterns": (self.error_patterns, list),
            "behavioral_observations": (self.behavioral_observations, list),
        }
        for field_name, (value, expected_type) in json_types.items():
            if not isinstance(value, expected_type):
                errors[field_name] = f"Must be a structured {expected_type.__name__}."

        if self.child_id and self.child.school_id and self.center_id != self.child.school_id:
            errors["center"] = "Session must use the child's center."
        if self.child_id and not self.child.idea_services_authorized:
            errors["child"] = (
                "Recorded parent consent and IEP-team approval are required before services can be scheduled."
            )
        if self.curriculum_position_id and self.center_id != self.curriculum_position.center_id:
            errors["curriculum_position"] = "Curriculum position must belong to the session center."
        if self.started_at and self.ended_at and self.ended_at <= self.started_at:
            errors["ended_at"] = "End time must be after start time."
        if self.accuracy_denominator is not None:
            if self.accuracy_denominator == 0:
                errors["accuracy_denominator"] = "Accuracy denominator must be greater than zero."
            elif self.accuracy_numerator is not None and self.accuracy_numerator > self.accuracy_denominator:
                errors["accuracy_numerator"] = "Accuracy numerator cannot exceed the denominator."
        if (self.accuracy_numerator is None) != (self.accuracy_denominator is None):
            errors["accuracy_numerator"] = "Accuracy numerator and denominator must be supplied together."
        if self.curriculum_position_id:
            curriculum_code = self.curriculum_position.curriculum.code
            is_pfr_part = self.intervention_part in {
                self.InterventionPart.PFR_1A,
                self.InterventionPart.PFR_1B,
            }
            if curriculum_code == "pfr" and not is_pfr_part:
                errors["intervention_part"] = "PFR sessions must be recorded as Session 1a or Session 1b."
            if curriculum_code == "og_plus" and self.intervention_part != self.InterventionPart.OG_CONCEPT:
                errors["intervention_part"] = "OG+ sessions must use the concept-aligned session type."
        if self.status == self.Status.COMPLETED:
            required_values = {
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "activities_completed": self.activities_completed,
                "item_sets": self.item_sets,
                "accuracy_rate": self.accuracy_rate,
                "accuracy_numerator": self.accuracy_numerator,
                "accuracy_denominator": self.accuracy_denominator,
                "time_to_mastery_signals": self.time_to_mastery_signals,
                "next_session_direction": self.next_session_direction.strip(),
                "home_practice_suggestion": self.home_practice_suggestion.strip(),
            }
            for field_name, value in required_values.items():
                if value is None or value == "" or value == [] or value == {}:
                    errors[field_name] = "Required when a session is completed."
            self._validate_structured_capture(errors)
        if errors:
            raise ValidationError(errors)

    def _validate_structured_capture(self, errors):
        activity_statuses = {"completed", "partial", "not_completed"}
        for index, activity in enumerate(self.activities_completed):
            if not isinstance(activity, dict):
                errors["activities_completed"] = f"Activity {index + 1} must be an object."
                break
            required = {"code", "status", "minutes", "item_set_id"}
            if not required.issubset(activity):
                errors["activities_completed"] = f"Activity {index + 1} is missing required structured fields."
                break
            if activity.get("status") not in activity_statuses:
                errors["activities_completed"] = f"Activity {index + 1} has an unsupported status."
                break

        allowed_behavior_codes = {
            "task_persistence",
            "attention_to_print",
            "response_latency",
            "self_correction",
            "requests_break",
            "uses_strategy",
            "confidence_to_attempt",
        }
        allowed_ratings = {"rare", "emerging", "inconsistent", "consistent"}
        for observation in self.behavioral_observations:
            if not isinstance(observation, dict) or observation.get("code") not in allowed_behavior_codes:
                errors["behavioral_observations"] = "Behavioral observations must use an allowed observable code."
                break
            if observation.get("rating") not in allowed_ratings:
                errors["behavioral_observations"] = "Behavioral observations must use an allowed rating."
                break

        for pattern in self.error_patterns:
            if not isinstance(pattern, dict) or not {"code", "count", "opportunities"}.issubset(pattern):
                errors["error_patterns"] = "Each error pattern requires code, count, and opportunities."
                break

        mastery_keys = {
            "cumulative_sessions_at_position",
            "first_attempt_accuracy",
            "latest_accuracy",
            "prompts_per_10_items",
            "independent_transfer",
            "reteach",
        }
        if not mastery_keys.issubset(self.time_to_mastery_signals):
            errors["time_to_mastery_signals"] = "Time-to-mastery signals are incomplete."

    def __str__(self):
        return f"{self.child} - {self.get_intervention_part_display()} on {self.scheduled_start:%Y-%m-%d}"


class SessionRevision(TimestampedModel):
    """Immutable structured snapshot of a session edit (PRD FR-2.1, FR-5.1)."""

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="revision_history")
    center = models.ForeignKey(
        "schools.School",
        on_delete=models.PROTECT,
        related_name="intervention_session_revisions",
    )
    revision = models.PositiveIntegerField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="intervention_session_revisions",
    )
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["session", "-revision"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "revision"],
                name="unique_intervention_session_revision",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "created_at"]),
            models.Index(fields=["session", "revision"]),
        ]

    def __str__(self):
        return f"Session {self.session_id} revision {self.revision}"
