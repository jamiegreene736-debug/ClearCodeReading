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
    evidence_considered = models.JSONField(default=dict, blank=True)
    source_recommendation = models.ForeignKey(
        "PlacementRecommendation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="placement_overrides",
    )
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


class PlacementEvidence(AuditedModel):
    """Structured, reproducible input from a curriculum-embedded placement instrument."""

    class Instrument(models.TextChoices):
        PFR_PLACEMENT = "pfr_placement", "PFR Placement Test"
        OG_PA_DIAGNOSTIC = "og_pa_diagnostic", "OG+ Phonological Awareness Diagnostic"
        OG_BENCHMARK = "og_benchmark", "OG+ Benchmark Assessment"
        OG_SPELLING_SURVEY = "og_spelling_survey", "OG+ Informal Spelling Survey"

    class Source(models.TextChoices):
        MANUAL = "manual", "Structured manual entry"
        IMPORT = "import", "Structured import"
        READING_SURVEY = "reading_survey", "Digital Reading Survey context"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        COMPLETED = "completed", "Completed"

    child = models.ForeignKey(
        "users.ChildProfile",
        on_delete=models.PROTECT,
        related_name="placement_evidence",
    )
    curriculum = models.ForeignKey(Curriculum, on_delete=models.PROTECT, related_name="placement_evidence")
    source_assessment = models.ForeignKey(
        "assessments.Assessment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="placement_evidence",
    )
    instrument = models.CharField(max_length=32, choices=Instrument.choices, db_index=True)
    source = models.CharField(max_length=24, choices=Source.choices, default=Source.MANUAL, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED, db_index=True)
    assessment_version = models.CharField(max_length=80)
    administered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="placement_evidence_administered",
    )
    administered_at = models.DateTimeField(default=timezone.now, db_index=True)
    instructional_grade_band = models.CharField(max_length=32, blank=True, db_index=True)
    raw_results = models.JSONField(default=dict)
    supporting_context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-administered_at", "-created_at"]
        indexes = [
            models.Index(fields=["center", "child", "administered_at"]),
            models.Index(fields=["center", "instrument", "status"]),
            models.Index(fields=["curriculum", "administered_at"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.child_id and self.child.school_id and self.center_id != self.child.school_id:
            errors["center"] = "Placement evidence must use the child's center."
        if self.curriculum_id and self.curriculum.center_id != self.center_id:
            errors["curriculum"] = "Placement evidence and curriculum must belong to the same center."
        expected_code = (
            Curriculum.Code.PFR
            if self.instrument == self.Instrument.PFR_PLACEMENT
            else Curriculum.Code.OG_PLUS
        )
        if self.curriculum_id and self.curriculum.code != expected_code:
            errors["curriculum"] = "The selected curriculum does not match the placement instrument."
        if self.status == self.Status.COMPLETED and self.curriculum_id and self.curriculum.code == Curriculum.Code.OG_PLUS:
            if not self.instructional_grade_band:
                errors["instructional_grade_band"] = "OG+ placement requires the instructional grade band."
            expected_instruments = {
                "pre_k": {self.Instrument.OG_PA_DIAGNOSTIC},
                "kindergarten": {self.Instrument.OG_PA_DIAGNOSTIC},
                "grade_1": {self.Instrument.OG_BENCHMARK},
                "grade_2": {self.Instrument.OG_BENCHMARK},
                "grade_3": {self.Instrument.OG_SPELLING_SURVEY},
                "grade_4": {self.Instrument.OG_SPELLING_SURVEY},
                "grade_5": {self.Instrument.OG_SPELLING_SURVEY},
                "other": {self.Instrument.OG_SPELLING_SURVEY},
            }
            allowed = expected_instruments.get(self.instructional_grade_band)
            if allowed and self.instrument not in allowed:
                errors["instrument"] = "The OG+ instrument does not match the instructional grade band."
        if not isinstance(self.raw_results, dict):
            errors["raw_results"] = "Placement results must be a structured object."
        if not isinstance(self.supporting_context, dict):
            errors["supporting_context"] = "Supporting context must be a structured object."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.get_instrument_display()} for {self.child}"


class PlacementRecommendation(AuditedModel):
    """Editable specialist decision generated from deterministic placement evidence."""

    class Decision(models.TextChoices):
        PLACE = "place", "Place at sequence position"
        SPECIALIST_REVIEW = "specialist_review", "Specialist review required"
        CURRICULUM_COMPLETE = "curriculum_complete", "Curriculum completion review"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending specialist decision"
        CONFIRMED = "confirmed", "Confirmed"
        OVERRIDDEN = "overridden", "Overridden"

    evidence = models.OneToOneField(
        PlacementEvidence,
        on_delete=models.PROTECT,
        related_name="recommendation",
    )
    recommended_curriculum = models.ForeignKey(
        Curriculum,
        on_delete=models.PROTECT,
        related_name="placement_recommendations",
    )
    recommended_position = models.ForeignKey(
        CurriculumSequence,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="placement_recommendations",
    )
    decision = models.CharField(max_length=24, choices=Decision.choices, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    deficit_profile = models.JSONField(default=list, blank=True)
    rule_trace = models.JSONField(default=dict)
    rationale = models.TextField()
    advisory_narrative = models.TextField(blank=True)
    ai_metadata = models.JSONField(default=dict, blank=True)
    final_position = models.ForeignKey(
        CurriculumSequence,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="finalized_placement_recommendations",
    )
    final_curriculum = models.ForeignKey(
        Curriculum,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="finalized_placement_recommendations",
    )
    override_rationale = models.TextField(blank=True)
    evidence_considered = models.JSONField(default=dict, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="placement_recommendations_confirmed",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resulting_placement = models.ForeignKey(
        StudentPlacement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_recommendations",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["center", "status", "created_at"]),
            models.Index(fields=["recommended_curriculum", "recommended_position"]),
            models.Index(fields=["confirmed_by", "confirmed_at"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.evidence_id and self.evidence.center_id != self.center_id:
            errors["center"] = "Recommendation and evidence must belong to the same center."
        if self.recommended_curriculum_id and self.recommended_curriculum.center_id != self.center_id:
            errors["recommended_curriculum"] = "Recommendation curriculum must belong to the same center."
        if self.recommended_position_id and self.recommended_position.curriculum_id != self.recommended_curriculum_id:
            errors["recommended_position"] = "Position must belong to the recommended curriculum."
        if self.final_position_id:
            final_curriculum_id = self.final_curriculum_id or self.recommended_curriculum_id
            if self.final_position.curriculum_id != final_curriculum_id:
                errors["final_position"] = "Final position must belong to the final curriculum."
        if self.decision == self.Decision.PLACE and not self.recommended_position_id:
            errors["recommended_position"] = "A placement recommendation requires a position."
        if self.status == self.Status.OVERRIDDEN and not self.override_rationale.strip():
            errors["override_rationale"] = "An override rationale is required."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Recommendation for {self.evidence.child} ({self.get_status_display()})"


class RecommendedSequencePosition(TimestampedModel):
    """Ranked, queryable sequence output used by specialists and future grouping."""

    recommendation = models.ForeignKey(
        PlacementRecommendation,
        on_delete=models.CASCADE,
        related_name="recommended_sequence",
    )
    position = models.ForeignKey(
        CurriculumSequence,
        on_delete=models.PROTECT,
        related_name="ranked_recommendations",
    )
    priority = models.PositiveSmallIntegerField()
    gap_codes = models.JSONField(default=list, blank=True)
    rationale = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["recommendation", "priority"]
        constraints = [
            models.UniqueConstraint(
                fields=["recommendation", "priority"],
                name="unique_recommendation_priority",
            ),
            models.UniqueConstraint(
                fields=["recommendation", "position"],
                name="unique_recommendation_position",
            ),
        ]
        indexes = [
            models.Index(fields=["recommendation", "priority"]),
            models.Index(fields=["position", "priority"]),
        ]

    def __str__(self):
        return f"{self.recommendation_id}: {self.priority} - {self.position.code}"


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
