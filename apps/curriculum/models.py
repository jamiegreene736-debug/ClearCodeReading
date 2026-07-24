from django.db import models
from django.db.models import Q
from django.conf import settings
from django.core.exceptions import ValidationError
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
    """Center-scoped revision metadata used by Phase 0 instructional records."""

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


class Skill(TimestampedModel, SoftDeleteModel):
    """Legacy generic skill taxonomy retained for existing API compatibility."""

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


class Curriculum(AuditedModel):
    """A center's versioned instructional methodology catalog (PRD FR-0.1)."""

    class Code(models.TextChoices):
        PFR = "pfr", "Phonics for Reading"
        OG_PLUS = "og_plus", "IMSE Comprehensive Orton-Gillingham Plus"

    code = models.CharField(max_length=20, choices=Code.choices, db_index=True)
    name = models.CharField(max_length=255)
    version = models.CharField(max_length=40, default="2026.1")
    is_active = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["center__name", "code", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["center", "code", "version"],
                name="unique_center_curriculum_version",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "code", "is_active"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.center}: {self.get_code_display()} ({self.version})"


class CurriculumSequence(AuditedModel):
    """An ordered PFR lesson or OG+ concept in the frozen skill graph (FR-0.1)."""

    class PFRLevel(models.TextChoices):
        A = "A", "Level A"
        B = "B", "Level B"
        C = "C", "Level C"

    class PositionType(models.TextChoices):
        LETTER_SOUND = "letter_sound", "Letter / Sound"
        WORD_TYPE = "word_type", "Word Type"
        SYLLABLE_TYPE = "syllable_type", "Syllable Type"
        HIGH_FREQUENCY_WORD = "high_frequency_word", "High-Frequency Word"
        PHONOLOGICAL_AWARENESS = "phonological_awareness", "Phonological Awareness"
        PHONICS_CONCEPT = "phonics_concept", "Phonics Concept"
        ORTHOGRAPHIC_RULE = "orthographic_rule", "Orthographic Rule"
        MORPHOLOGY = "morphology", "Morphology"
        CHECKPOINT = "checkpoint", "Instructional Checkpoint"

    curriculum = models.ForeignKey(Curriculum, on_delete=models.CASCADE, related_name="positions")
    code = models.CharField(max_length=80)
    sequence_order = models.PositiveIntegerField()
    level = models.CharField(max_length=1, choices=PFRLevel.choices, blank=True, db_index=True)
    lesson_number = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    concept_number = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    title = models.CharField(max_length=255)
    position_type = models.CharField(max_length=32, choices=PositionType.choices, db_index=True)
    description = models.TextField(blank=True)
    letter_sounds = models.JSONField(default=list, blank=True)
    word_types = models.JSONField(default=list, blank=True)
    syllable_types = models.JSONField(default=list, blank=True)
    high_frequency_words = models.JSONField(default=list, blank=True)
    red_words_spell_and_read = models.JSONField(default=list, blank=True)
    red_words_read_only = models.JSONField(default=list, blank=True)
    activities = models.JSONField(default=list, blank=True)
    item_set_schema = models.JSONField(default=dict, blank=True)
    mastery_criteria = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    prerequisites = models.ManyToManyField(
        "self",
        blank=True,
        symmetrical=False,
        related_name="unlocks_positions",
    )

    class Meta:
        ordering = ["curriculum", "sequence_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["curriculum", "code"], name="unique_curriculum_position_code"),
            models.UniqueConstraint(
                fields=["curriculum", "sequence_order"],
                name="unique_curriculum_sequence_order",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(level="") & Q(lesson_number__isnull=True) & Q(concept_number__isnull=False))
                    | (Q(level__in=["A", "B", "C"]) & Q(lesson_number__isnull=False) & Q(concept_number__isnull=True))
                ),
                name="sequence_has_pfr_lesson_or_og_concept",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "curriculum", "sequence_order"]),
            models.Index(fields=["curriculum", "level", "lesson_number"]),
            models.Index(fields=["curriculum", "concept_number"]),
            models.Index(fields=["position_type", "is_deleted"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        if self.curriculum_id and self.center_id != self.curriculum.center_id:
            raise ValidationError({"center": "Position and curriculum must belong to the same center."})

        if not self.curriculum_id:
            return
        if self.curriculum.code == Curriculum.Code.PFR and (
            not self.level or self.lesson_number is None or self.concept_number is not None
        ):
            raise ValidationError("PFR positions require a level and lesson number only.")
        if self.curriculum.code == Curriculum.Code.OG_PLUS and (
            self.level or self.lesson_number is not None or self.concept_number is None
        ):
            raise ValidationError("OG+ positions require a concept number only.")

    def __str__(self):
        return f"{self.code} - {self.title}"


class StudentPlacement(AuditedModel):
    """A child's single active methodology and current sequence position (FR-1.2)."""

    child = models.ForeignKey(
        "users.ChildProfile",
        on_delete=models.CASCADE,
        related_name="curriculum_placements",
    )
    curriculum = models.ForeignKey(Curriculum, on_delete=models.PROTECT, related_name="student_placements")
    current_position = models.ForeignKey(
        CurriculumSequence,
        on_delete=models.PROTECT,
        related_name="current_student_placements",
    )
    methodology_rationale = models.TextField()
    placement_evidence = models.JSONField(default=dict, blank=True)
    placed_at = models.DateTimeField(default=timezone.now, db_index=True)
    placed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_placements_made",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["child__last_name", "child__first_name", "-placed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["child"],
                condition=Q(is_active=True, is_deleted=False),
                name="unique_active_child_methodology",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "curriculum", "is_active"]),
            models.Index(fields=["child", "is_active", "is_deleted"]),
            models.Index(fields=["current_position", "is_active"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.child_id and self.child.school_id and self.center_id != self.child.school_id:
            errors["center"] = "Placement must use the child's center."
        if self.curriculum_id and self.center_id != self.curriculum.center_id:
            errors["curriculum"] = "Placement and curriculum must belong to the same center."
        if self.current_position_id and self.current_position.curriculum_id != self.curriculum_id:
            errors["current_position"] = "Current position must belong to the selected curriculum."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.child} - {self.curriculum.get_code_display()} at {self.current_position.code}"


class StudentPlacementOverride(AuditedModel):
    """Immutable specialist-authored placement change history (PRD FR-1.2)."""

    placement = models.ForeignKey(StudentPlacement, on_delete=models.CASCADE, related_name="override_history")
    previous_position = models.ForeignKey(
        CurriculumSequence,
        on_delete=models.PROTECT,
        related_name="placement_overrides_from",
    )
    new_position = models.ForeignKey(
        CurriculumSequence,
        on_delete=models.PROTECT,
        related_name="placement_overrides_to",
    )
    rationale = models.TextField()
    specialist = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="placement_overrides",
    )
    overridden_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-overridden_at", "-created_at"]
        indexes = [
            models.Index(fields=["center", "placement", "overridden_at"]),
            models.Index(fields=["specialist", "overridden_at"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.placement_id and self.center_id != self.placement.center_id:
            errors["center"] = "Override and placement must belong to the same center."
        if self.placement_id:
            curriculum_id = self.placement.curriculum_id
            if self.previous_position_id and self.previous_position.curriculum_id != curriculum_id:
                errors["previous_position"] = "Previous position must use the placement curriculum."
            if self.new_position_id and self.new_position.curriculum_id != curriculum_id:
                errors["new_position"] = "New position must use the placement curriculum."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.placement}: {self.previous_position.code} to {self.new_position.code}"


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
