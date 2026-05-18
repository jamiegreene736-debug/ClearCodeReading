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
    survey_completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    raw_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    max_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    overall_score = models.IntegerField(null=True, blank=True, db_index=True)
    reading_age = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, db_index=True)
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
            models.Index(fields=["survey_completed_at"]),
            models.Index(fields=["overall_score", "reading_age"]),
            models.Index(fields=["completed_at"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.title} for {self.child}"


class AssessmentQuestion(TimestampedModel, SoftDeleteModel):
    class Category(models.TextChoices):
        PHONEMIC_AWARENESS = "phonemic_awareness", "Phonemic Awareness"
        LETTER_SOUND = "letter_sound", "Letter Sounds"
        PHONICS = "phonics", "Phonics / Decoding"
        ADVANCED_PHONICS = "advanced_phonics", "Advanced Decoding"
        SIGHT_WORDS = "sight_words", "Sight Words"
        FLUENCY = "fluency", "Fluency"
        VOCABULARY = "vocabulary", "Vocabulary"
        COMPREHENSION = "comprehension", "Comprehension"
        WRITING_READINESS = "writing_readiness", "Writing Readiness"
        CONFIDENCE = "confidence", "Reading Confidence"

    class Difficulty(models.TextChoices):
        PRE_READER = "pre_reader", "Pre-reader"
        EMERGING = "emerging", "Emerging"
        EARLY = "early", "Early"
        DEVELOPING = "developing", "Developing"
        FLUENT = "fluent", "Fluent"

    class QuestionType(models.TextChoices):
        MULTIPLE_CHOICE = "multiple_choice", "Multiple Choice"
        AUDIO_PROMPT = "audio_prompt", "Audio Prompt"
        IMAGE_PROMPT = "image_prompt", "Image Prompt"
        ORAL_READING = "oral_reading", "Oral Reading"
        FREE_RESPONSE = "free_response", "Free Response"
        RATING_SCALE = "rating_scale", "Rating Scale"

    category = models.CharField(max_length=40, choices=Category.choices, db_index=True)
    difficulty = models.CharField(max_length=32, choices=Difficulty.choices, db_index=True)
    question_type = models.CharField(max_length=32, choices=QuestionType.choices, db_index=True)
    question_text = models.TextField()
    audio_file = models.FileField(upload_to="assessment-audio/%Y/%m/%d/", blank=True)
    image_url = models.URLField(blank=True)
    correct_answer = models.JSONField(default=dict, blank=True)
    options = models.JSONField(default=list, blank=True)
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["category", "difficulty", "sort_order", "id"]
        indexes = [
            models.Index(fields=["category", "difficulty"]),
            models.Index(fields=["question_type", "is_active"]),
            models.Index(fields=["sort_order"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.get_category_display()} - {self.question_text[:60]}"


class QuestionOption(TimestampedModel, SoftDeleteModel):
    question = models.ForeignKey(AssessmentQuestion, on_delete=models.CASCADE, related_name="question_options")
    label = models.CharField(max_length=255)
    value = models.CharField(max_length=120, blank=True)
    is_correct = models.BooleanField(default=False, db_index=True)
    score_value = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["question", "sort_order", "id"]
        indexes = [
            models.Index(fields=["question", "sort_order"]),
            models.Index(fields=["question", "is_correct"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.question_id}: {self.label}"


class ChildAssessmentResponse(TimestampedModel, SoftDeleteModel):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="child_responses", null=True, blank=True)
    child = models.ForeignKey("users.ChildProfile", on_delete=models.CASCADE, related_name="assessment_responses")
    question = models.ForeignKey(AssessmentQuestion, on_delete=models.PROTECT, related_name="child_responses")
    selected_option = models.ForeignKey(QuestionOption, on_delete=models.SET_NULL, null=True, blank=True, related_name="child_responses")
    answer = models.JSONField(default=dict, blank=True)
    is_correct = models.BooleanField(null=True, blank=True, db_index=True)
    score_value = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    time_taken = models.PositiveIntegerField(help_text="Time taken in seconds.", null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["assessment", "created_at"]
        indexes = [
            models.Index(fields=["assessment", "question"]),
            models.Index(fields=["child", "question"]),
            models.Index(fields=["question", "is_correct"]),
            models.Index(fields=["time_taken"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.child} response to {self.question_id}"


class AssessmentResult(TimestampedModel, SoftDeleteModel):
    assessment = models.OneToOneField(Assessment, on_delete=models.CASCADE, related_name="result")
    final_scores = models.JSONField(default=dict, blank=True)
    reading_age = models.DecimalField(max_digits=4, decimal_places=1, db_index=True)
    grade_equivalent = models.CharField(max_length=40, blank=True, db_index=True)
    category_breakdown = models.JSONField(default=dict, blank=True)
    strengths = models.JSONField(default=list, blank=True)
    growth_areas = models.JSONField(default=list, blank=True)
    teacher_summary = models.TextField(blank=True)
    evaluator_notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    # Simple scoring lookup used by the digital survey:
    # total_score_percent 0-20 -> reading_age 4.0
    # 21-35 -> 5.0, 36-50 -> 6.0, 51-65 -> 7.0,
    # 66-80 -> 8.0, 81-90 -> 9.0, 91-100 -> 10.0+.
    # The current continuous formula mirrors that table:
    # reading_age = clamp(4.0 + (overall_score_percent / 100) * 7.0, 4.0, 11.0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["assessment"]),
            models.Index(fields=["reading_age"]),
            models.Index(fields=["grade_equivalent"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"Result for {self.assessment} - age {self.reading_age}"


class AssessmentAudioAsset(TimestampedModel):
    key = models.SlugField(max_length=80, unique=True)
    text = models.TextField()
    audio = models.BinaryField()
    content_type = models.CharField(max_length=80, default="audio/mpeg")
    provider = models.CharField(max_length=80, default="elevenlabs", db_index=True)
    voice_id = models.CharField(max_length=120, blank=True)
    model_id = models.CharField(max_length=120, blank=True)
    output_format = models.CharField(max_length=80, blank=True)
    byte_length = models.PositiveIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["key"]
        indexes = [
            models.Index(fields=["provider", "key"]),
            models.Index(fields=["checksum"]),
        ]

    def __str__(self):
        return f"{self.key} ({self.provider})"
