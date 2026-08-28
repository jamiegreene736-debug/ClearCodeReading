from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models


def recruiting_document_upload_path(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"recruiting/documents/{uuid4().hex}{extension}"


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class RecruitingInterest(TimeStampedModel):
    class CareerPath(models.TextChoices):
        TEACHER = "teacher", "Teaching or reading specialist"
        COMPANY = "company", "Company team"

    class Status(models.TextChoices):
        NEW = "new", "New"
        REVIEWING = "reviewing", "Reviewing"
        CLOSED = "closed", "Closed"

    name = models.CharField(max_length=255)
    email = models.EmailField(db_index=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)
    how_heard = models.CharField(max_length=255, blank=True)
    resume = models.FileField(upload_to=recruiting_document_upload_path, blank=True)
    resume_data = models.BinaryField(blank=True, default=bytes)
    resume_content_type = models.CharField(max_length=100, blank=True)
    resume_original_name = models.CharField(max_length=255, blank=True)
    cover_letter = models.FileField(upload_to=recruiting_document_upload_path, blank=True)
    cover_letter_data = models.BinaryField(blank=True, default=bytes)
    cover_letter_content_type = models.CharField(max_length=100, blank=True)
    cover_letter_original_name = models.CharField(max_length=255, blank=True)
    career_path = models.CharField(max_length=16, choices=CareerPath.choices, db_index=True)
    role_interest = models.CharField(max_length=255)
    notes = models.TextField()
    source_path = models.CharField(max_length=255, default="/careers/")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW, db_index=True)
    candidate_pool = models.CharField(max_length=255, default="ClearCode recruiting")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_recruiting_interests",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="core_recruit_status_created"),
            models.Index(fields=["career_path", "status"], name="core_recruit_path_status"),
        ]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} — {self.get_career_path_display()}"
