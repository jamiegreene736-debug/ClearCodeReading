"""Private calendar settings belonging to an individual consultation host."""

import uuid

from django.conf import settings
from django.db import models


class HostCalendar(models.Model):
    host = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    encrypted_url = models.TextField(blank=True)
    source_timezone = models.CharField(max_length=64, default="America/New_York")
    subscription_token = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)
