from django.conf import settings
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


class Assessment(TimestampedModel, SoftDeleteModel):
    class AssessmentType(models.TextChoices):
        SCREENING = "screening", "Screening"
        DIAGNOSTIC = "diagnostic", "Diagnostic"
        FORMATIVE = "formative", "Formative"
        SUMMATIVE = "summative", "Summative"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        HUMAN_REVIEW = "human_review", "Human Review"
        COMPLETED = "completed", "Completed"
        ARCHIVED = "archived", "Archived"

    child = models.ForeignKey("users.ChildProfile", on_delete=models.CASCADE, related_name="assessments")
    school = models.ForeignKey("schools.School", on_delete=models.SET_NULL, null=True, blank=True, related_name="assessments")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_assessments")
    assessment_type = models.CharField(max_length=32, choices=AssessmentType.choices, db_index=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING, db_index=True)
    title = models.CharField(max_length=255)
    skill = models.ForeignKey("curriculum.Skill", on_delete=models.SET_NULL, null=True, blank=True, related_name="assessments")
    scheduled_for = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    raw_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    max_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    percentile = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    responses = models.JSONField(default=dict, blank=True)
    scoring = models.JSONField(default=dict, blank=True)
    recommendations = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-scheduled_for", "-created_at"]
        indexes = [
            models.Index(fields=["child", "status"]),
            models.Index(fields=["school", "assessment_type"]),
            models.Index(fields=["skill", "status"]),
            models.Index(fields=["completed_at"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.title} for {self.child}"
