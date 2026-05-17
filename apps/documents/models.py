from django.db import models

from apps.core.models import TimeStampedModel
from apps.readings.models import ReadingSession


class Document(TimeStampedModel):
    session = models.ForeignKey(ReadingSession, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to="documents/%Y/%m/%d/", blank=True)
    content = models.TextField(blank=True)

    def __str__(self):
        return self.title
