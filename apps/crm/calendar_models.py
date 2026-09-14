"""Private calendar settings belonging to an individual consultation host."""

import uuid

from django.conf import settings
from django.db import models


class HostCalendar(models.Model):
    host = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    google_email = models.EmailField(blank=True)
    encrypted_google_refresh_token = models.TextField(blank=True)
    encrypted_url = models.TextField(blank=True)
    source_timezone = models.CharField(max_length=64, default="America/New_York")
    subscription_token = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)


class CalendarAuthorization(models.Model):
    state_hash = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    session_hash = models.CharField(max_length=64)
    encrypted_verifier = models.TextField()
    nonce = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
