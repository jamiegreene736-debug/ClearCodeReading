from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel
from apps.repositories.models import CodeFile, Repository


class ReadingSession(TimeStampedModel):
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name="reading_sessions")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True)

    def __str__(self):
        return self.title


class Annotation(TimeStampedModel):
    session = models.ForeignKey(ReadingSession, on_delete=models.CASCADE, related_name="annotations")
    file = models.ForeignKey(CodeFile, on_delete=models.CASCADE, related_name="annotations")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    start_line = models.PositiveIntegerField()
    end_line = models.PositiveIntegerField()
    body = models.TextField()

    def __str__(self):
        return f"{self.file.path}:{self.start_line}"
