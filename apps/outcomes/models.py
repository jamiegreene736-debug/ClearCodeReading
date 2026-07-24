import hashlib

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


FORBIDDEN_IDENTIFIER_KEYS = {
    "child_id",
    "child_ids",
    "child_name",
    "child_names",
    "student_id",
    "student_ids",
    "student_identifier",
    "student_identifiers",
    "specialist_id",
    "specialist_ids",
    "specialist_name",
    "specialist_names",
}


class DeIdentifiedOutcomeSnapshot(models.Model):
    """Immutable aggregate evidence for leadership and Foundation reporting."""

    class WindowType(models.TextChoices):
        MONTH = "month", "Month"
        QUARTER = "quarter", "Quarter"
        YEAR = "year", "Year"
        CUSTOM = "custom", "Custom"

    center = models.ForeignKey(
        "schools.School",
        on_delete=models.PROTECT,
        related_name="outcome_snapshots",
    )
    center_key = models.CharField(max_length=64, db_index=True)
    methodology = models.CharField(max_length=40, db_index=True)
    grade_band = models.CharField(max_length=32, db_index=True)
    window_type = models.CharField(max_length=16, choices=WindowType.choices, db_index=True)
    window_start = models.DateField(db_index=True)
    window_end = models.DateField(db_index=True)
    metric_scope = models.CharField(max_length=40, default="core_outcomes", db_index=True)
    aggregate_version = models.CharField(max_length=32, default="v1", db_index=True)
    privacy_floor = models.PositiveSmallIntegerField(default=5)
    metrics = models.JSONField(default=dict)
    source_counts = models.JSONField(default=dict, blank=True)
    generated_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-window_end", "center_key", "methodology", "grade_band", "-generated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "center",
                    "methodology",
                    "grade_band",
                    "window_type",
                    "window_start",
                    "window_end",
                    "metric_scope",
                    "aggregate_version",
                ],
                name="unique_outcome_snapshot_version",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "window_type", "window_end"]),
            models.Index(fields=["methodology", "grade_band", "window_end"]),
            models.Index(fields=["metric_scope", "aggregate_version"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Outcome snapshots are immutable; create a new aggregate_version instead.")
        if not self.center_key and self.center_id:
            self.center_key = build_center_key(self.center_id)
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        errors = {}
        cohort_size = self.metrics.get("cohort_students") if isinstance(self.metrics, dict) else None
        if not isinstance(cohort_size, int) or cohort_size < self.privacy_floor:
            errors["metrics"] = f"Outcome snapshots require a cohort of at least {self.privacy_floor} students."
        for field_name in ("metrics", "source_counts"):
            forbidden = _find_forbidden_identifier_key(getattr(self, field_name))
            if forbidden:
                errors[field_name] = f"De-identified outcomes cannot contain identifier key '{forbidden}'."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.center_key} {self.methodology} {self.grade_band} {self.window_start} to {self.window_end}"


def build_center_key(center_id):
    return hashlib.sha256(f"clear-code-reading:center:{center_id}".encode("utf-8")).hexdigest()[:16]


def _find_forbidden_identifier_key(value):
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in FORBIDDEN_IDENTIFIER_KEYS:
                return normalized_key
            nested_match = _find_forbidden_identifier_key(nested_value)
            if nested_match:
                return nested_match
    elif isinstance(value, list):
        for item in value:
            nested_match = _find_forbidden_identifier_key(item)
            if nested_match:
                return nested_match
    return None
