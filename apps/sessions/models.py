from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
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
    session_template = models.ForeignKey(
        "SessionTemplate",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
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
        if self.session_template_id:
            template = self.session_template
            if self.center_id != template.center_id:
                errors["session_template"] = "Session template must belong to the session center."
            elif self.curriculum_position_id and template.curriculum_id != self.curriculum_position.curriculum_id:
                errors["session_template"] = "Session template must use the session curriculum."
            elif template.curriculum_position_id and template.curriculum_position_id != self.curriculum_position_id:
                errors["session_template"] = "Session template must match the session curriculum position."
            elif template.session_part != self.intervention_part:
                errors["session_template"] = "Session template must match the intervention part."
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
            if self.session_template_id:
                self.session_template.validate_capture(self, errors)
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


class SessionTemplate(AuditedModel):
    """Versioned, methodology-specific contract for specialist session capture."""

    curriculum = models.ForeignKey(
        "curriculum.Curriculum",
        on_delete=models.PROTECT,
        related_name="session_templates",
    )
    curriculum_position = models.ForeignKey(
        "curriculum.CurriculumSequence",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="session_templates",
        help_text="Optional lesson or concept this capture contract is specific to.",
    )
    session_part = models.CharField(max_length=20, choices=Session.InterventionPart.choices, db_index=True)
    capture_fields = models.JSONField(
        default=dict,
        help_text="JSON schema for the structured fields shown and required by this session form.",
    )
    title = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True, db_index=True)
    version = models.PositiveIntegerField(default=1)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["curriculum", "session_part", "-version", "curriculum_position_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["center", "curriculum", "curriculum_position", "session_part", "version"],
                condition=Q(curriculum_position__isnull=False),
                name="unique_position_session_template_version",
            ),
            models.UniqueConstraint(
                fields=["center", "curriculum", "session_part", "version"],
                condition=Q(curriculum_position__isnull=True),
                name="unique_generic_session_template_version",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "curriculum", "session_part", "is_active"]),
            models.Index(fields=["curriculum_position", "session_part", "is_active"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.curriculum_id and self.center_id != self.curriculum.center_id:
            errors["center"] = "Session template and curriculum must belong to the same center."
        if self.curriculum_position_id:
            if self.center_id != self.curriculum_position.center_id:
                errors["curriculum_position"] = "Session template position must belong to the template center."
            elif self.curriculum_id != self.curriculum_position.curriculum_id:
                errors["curriculum_position"] = "Session template position must belong to the template curriculum."
        if not isinstance(self.capture_fields, dict):
            errors["capture_fields"] = "Capture fields must be a JSON object."
        else:
            required = self.capture_fields.get("required", [])
            properties = self.capture_fields.get("properties", {})
            session_field_names = {field.name for field in Session._meta.fields}
            if not isinstance(required, list) or not all(isinstance(field, str) for field in required):
                errors["capture_fields"] = "Capture field requirements must be a list of field names."
            elif not isinstance(properties, dict):
                errors["capture_fields"] = "Capture field properties must be a JSON object."
            elif any(not isinstance(config, dict) for config in properties.values()):
                errors["capture_fields"] = "Each capture field property must be a JSON object."
            elif unknown_fields := (set(required) | set(properties)) - session_field_names:
                errors["capture_fields"] = (
                    f"Capture fields must use Session fields; unknown: {', '.join(sorted(unknown_fields))}."
                )
        if not isinstance(self.metadata, dict):
            errors["metadata"] = "Metadata must be a JSON object."
        if errors:
            raise ValidationError(errors)

    def validate_capture(self, session, errors):
        """Add template-specific completion errors without replacing Session's contract."""
        required = self.capture_fields.get("required", [])
        properties = self.capture_fields.get("properties", {})
        for field_name in required:
            value = getattr(session, field_name, None)
            if value is None or value == "" or value == [] or value == {}:
                errors.setdefault(field_name, f"Required by session template {self.title!r}.")

        expected_types = {"array": list, "object": dict, "string": str}
        for field_name, config in properties.items():
            value = getattr(session, field_name, None)
            expected_type = expected_types.get(config.get("type"))
            if value is not None and expected_type and not isinstance(value, expected_type):
                errors.setdefault(field_name, f"Must match the {config['type']} capture field type.")
                continue
            required_keys = config.get("required_keys", [])
            if isinstance(value, dict) and required_keys:
                missing = [key for key in required_keys if key not in value]
                if missing:
                    errors.setdefault(
                        field_name,
                        f"Template requires structured sections: {', '.join(missing)}.",
                    )

        allowed_activity_codes = self.capture_fields.get("allowed_activity_codes")
        if allowed_activity_codes and isinstance(session.activities_completed, list):
            invalid_codes = sorted(
                {
                    activity.get("code")
                    for activity in session.activities_completed
                    if isinstance(activity, dict)
                    and activity.get("code")
                    and activity.get("code") not in allowed_activity_codes
                }
            )
            if invalid_codes:
                errors.setdefault(
                    "activities_completed",
                    f"Activities are not part of this session template: {', '.join(invalid_codes)}.",
                )

    def __str__(self):
        position = f" - {self.curriculum_position.code}" if self.curriculum_position_id else ""
        return f"{self.title}{position} (v{self.version})"


class SkillObservation(AuditedModel):
    """Queryable instructional evidence projected from a completed session."""

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="skill_observations")
    child = models.ForeignKey(
        "users.ChildProfile",
        on_delete=models.PROTECT,
        related_name="skill_observations",
    )
    curriculum_position = models.ForeignKey(
        "curriculum.CurriculumSequence",
        on_delete=models.PROTECT,
        related_name="skill_observations",
    )
    accuracy_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    response_rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    error_pattern_tags = models.JSONField(default=list, blank=True)
    time_signals = models.JSONField(default=dict, blank=True)
    activities = models.JSONField(default=list, blank=True)
    item_references = models.JSONField(default=list, blank=True)
    source_session_revision = models.PositiveIntegerField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-session__scheduled_start", "curriculum_position__sequence_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "curriculum_position"],
                name="unique_session_skill_observation",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "child"]),
            models.Index(fields=["child", "curriculum_position"]),
            models.Index(fields=["session", "curriculum_position"]),
            models.Index(fields=["center", "created_at"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.session_id:
            if self.center_id != self.session.center_id:
                errors["center"] = "Skill observation and session must belong to the same center."
            if self.child_id != self.session.child_id:
                errors["child"] = "Skill observation must use the session child."
        if self.curriculum_position_id:
            if self.center_id != self.curriculum_position.center_id:
                errors["curriculum_position"] = "Skill observation position must belong to the observation center."
            elif (
                self.session_id
                and self.curriculum_position.curriculum_id != self.session.curriculum_position.curriculum_id
            ):
                errors["curriculum_position"] = "Skill observation position must use the session curriculum."
        json_types = {
            "error_pattern_tags": (self.error_pattern_tags, list),
            "time_signals": (self.time_signals, dict),
            "activities": (self.activities, list),
            "item_references": (self.item_references, list),
            "metadata": (self.metadata, dict),
        }
        for field_name, (value, expected_type) in json_types.items():
            if not isinstance(value, expected_type):
                errors[field_name] = f"Must be a structured {expected_type.__name__}."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.child} - {self.curriculum_position.code} in session {self.session_id}"


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
