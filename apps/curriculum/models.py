from django.db import models
from django.conf import settings
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


class Skill(TimestampedModel, SoftDeleteModel):
    class Domain(models.TextChoices):
        PHONOLOGICAL_AWARENESS = "phonological_awareness", "Phonological Awareness"
        PHONICS = "phonics", "Phonics"
        FLUENCY = "fluency", "Fluency"
        VOCABULARY = "vocabulary", "Vocabulary"
        COMPREHENSION = "comprehension", "Comprehension"
        WRITING = "writing", "Writing"

    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    domain = models.CharField(max_length=40, choices=Domain.choices, db_index=True)
    grade_band = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    prerequisites = models.ManyToManyField("self", blank=True, symmetrical=False, related_name="unlocks")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["domain", "code"]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["domain", "grade_band"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Lesson(TimestampedModel, SoftDeleteModel):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=160, unique=True)
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="lessons")
    grade_level = models.CharField(max_length=64, blank=True, db_index=True)
    duration_minutes = models.PositiveIntegerField(default=20)
    objective = models.TextField(blank=True)
    content = models.JSONField(default=dict, blank=True)
    materials = models.JSONField(default=list, blank=True)
    differentiation = models.JSONField(default=dict, blank=True)
    is_published = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["skill__code", "title"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["skill", "is_published"]),
            models.Index(fields=["grade_level", "is_published"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return self.title


class TeachingAid(TimestampedModel, SoftDeleteModel):
    class AidType(models.TextChoices):
        WORKSHEET = "worksheet", "Worksheet"
        SLIDE_DECK = "slide_deck", "Slide Deck"
        MANIPULATIVE = "manipulative", "Manipulative"
        DECODABLE_TEXT = "decodable_text", "Decodable Text"
        ASSESSMENT_PROMPT = "assessment_prompt", "Assessment Prompt"
        OTHER = "other", "Other"

    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="teaching_aids", null=True, blank=True)
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="teaching_aids", null=True, blank=True)
    title = models.CharField(max_length=255)
    aid_type = models.CharField(max_length=32, choices=AidType.choices, db_index=True)
    file = models.FileField(upload_to="teaching-aids/%Y/%m/%d/", blank=True)
    url = models.URLField(blank=True)
    content = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["lesson", "aid_type"]),
            models.Index(fields=["skill", "aid_type"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return self.title


class LessonTemplate(TimestampedModel, SoftDeleteModel):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=160, unique=True)
    skill = models.ForeignKey(Skill, on_delete=models.SET_NULL, null=True, blank=True, related_name="lesson_templates")
    grade_band = models.CharField(max_length=64, blank=True, db_index=True)
    description = models.TextField(blank=True)
    goal = models.CharField(max_length=255, blank=True)
    recommended_minutes = models.PositiveIntegerField(default=15)
    activities = models.JSONField(default=list, blank=True)
    materials = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["grade_band", "is_active"]),
            models.Index(fields=["skill", "is_active"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return self.title


class TeacherLessonTemplate(TimestampedModel, SoftDeleteModel):
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assigned_lesson_templates",
        limit_choices_to={"role": "teacher"},
    )
    template = models.ForeignKey(LessonTemplate, on_delete=models.CASCADE, related_name="teacher_assignments")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="template_assignments_made",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["teacher__last_name", "template__title"]
        constraints = [
            models.UniqueConstraint(fields=["teacher", "template"], name="unique_teacher_lesson_template"),
        ]
        indexes = [
            models.Index(fields=["teacher", "is_deleted"]),
            models.Index(fields=["template", "is_deleted"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.template} -> {self.teacher}"


class ChildLessonAssignment(TimestampedModel, SoftDeleteModel):
    class Status(models.TextChoices):
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        PAUSED = "paused", "Paused"

    child = models.ForeignKey("users.ChildProfile", on_delete=models.CASCADE, related_name="lesson_assignments")
    template = models.ForeignKey(LessonTemplate, on_delete=models.PROTECT, related_name="child_assignments")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_lesson_assignments_made",
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ASSIGNED, db_index=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    teacher_notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "due_date", "-created_at"]
        indexes = [
            models.Index(fields=["child", "status"]),
            models.Index(fields=["template", "status"]),
            models.Index(fields=["assigned_by", "status"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.template} for {self.child}"
