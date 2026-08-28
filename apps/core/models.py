from django.db import models


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
    career_path = models.CharField(max_length=16, choices=CareerPath.choices, db_index=True)
    role_interest = models.CharField(max_length=255)
    notes = models.TextField()
    source_path = models.CharField(max_length=255, default="/careers/")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW, db_index=True)

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
