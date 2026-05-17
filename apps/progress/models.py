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


class Progress(TimestampedModel, SoftDeleteModel):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not Started"
        EMERGING = "emerging", "Emerging"
        DEVELOPING = "developing", "Developing"
        PROFICIENT = "proficient", "Proficient"
        MASTERED = "mastered", "Mastered"

    child = models.ForeignKey("users.ChildProfile", on_delete=models.CASCADE, related_name="progress_records")
    skill = models.ForeignKey("curriculum.Skill", on_delete=models.CASCADE, related_name="progress_records")
    school = models.ForeignKey("schools.School", on_delete=models.SET_NULL, null=True, blank=True, related_name="progress_records")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.NOT_STARTED, db_index=True)
    current_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    target_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_assessment = models.ForeignKey(
        "assessments.Assessment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="progress_updates",
    )
    evidence = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["child__last_name", "skill__code"]
        constraints = [
            models.UniqueConstraint(fields=["child", "skill"], name="unique_child_skill_progress"),
        ]
        indexes = [
            models.Index(fields=["child", "status"]),
            models.Index(fields=["skill", "status"]),
            models.Index(fields=["school", "status"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.child} - {self.skill}: {self.status}"


class MasteryRecord(TimestampedModel, SoftDeleteModel):
    child = models.ForeignKey("users.ChildProfile", on_delete=models.CASCADE, related_name="mastery_records")
    skill = models.ForeignKey("curriculum.Skill", on_delete=models.CASCADE, related_name="mastery_records")
    progress = models.ForeignKey(Progress, on_delete=models.CASCADE, related_name="mastery_records")
    assessment = models.ForeignKey(
        "assessments.Assessment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mastery_records",
    )
    mastered_at = models.DateTimeField(default=timezone.now, db_index=True)
    mastered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="mastery_records")
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-mastered_at"]
        indexes = [
            models.Index(fields=["child", "mastered_at"]),
            models.Index(fields=["skill", "mastered_at"]),
            models.Index(fields=["progress", "mastered_at"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.child} mastered {self.skill}"
