from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class SocialAuthorization(TimeStampedModel):
    """One sign-in attempt. The OAuth state is stored as a hash and expires quickly."""

    class Network(models.TextChoices):
        FACEBOOK = "facebook", "Facebook"
        INSTAGRAM = "instagram", "Instagram"

    state_hash = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="social_authorizations")
    session_hash = models.CharField(max_length=64)
    network = models.CharField(max_length=16, choices=Network.choices)
    code_used = models.BooleanField(default=False)
    consumed = models.BooleanField(default=False)
    awaiting_choice = models.BooleanField(default=False)
    encrypted_choices = models.TextField(blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.network} sign-in for {self.user_id}"


class SocialAccount(TimeStampedModel):
    """The ClearCode Reading presence on one network. There is at most one row per network."""

    class Network(models.TextChoices):
        FACEBOOK = "facebook", "Facebook"
        INSTAGRAM = "instagram", "Instagram"

    class Status(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        CONNECTED = "connected", "Connected"
        RECONNECT = "reconnect", "Reconnect required"

    network = models.CharField(max_length=16, choices=Network.choices, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DISCONNECTED)
    external_id = models.CharField(max_length=64, blank=True)
    display_name = models.CharField(max_length=200, blank=True)
    username = models.CharField(max_length=200, blank=True)
    encrypted_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    via_facebook = models.BooleanField(default=False)
    connected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="social_accounts_connected",
    )
    connected_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["network"]

    def __str__(self) -> str:
        return self.display_name or self.get_network_display()

    @property
    def is_connected(self) -> bool:
        return self.status == self.Status.CONNECTED and bool(self.encrypted_token)


class SocialPost(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        PUBLISHING = "publishing", "Publishing"
        POSTED = "posted", "Posted"
        ATTENTION = "attention", "Needs attention"

    class Source(models.TextChoices):
        BRIEF = "brief", "From a brief"
        MANUAL = "manual", "Written by hand"

    class Audience(models.TextChoices):
        FAMILIES = "families", "Families"
        TEACHERS = "teachers", "Teachers"
        GENERAL = "general", "General"

    class Tone(models.TextChoices):
        WARM = "warm", "Warm"
        PRACTICAL = "practical", "Practical"
        CELEBRATORY = "celebratory", "Celebratory"

    brief = models.TextField(blank=True)
    audience = models.CharField(max_length=16, choices=Audience.choices, default=Audience.FAMILIES)
    tone = models.CharField(max_length=16, choices=Tone.choices, default=Tone.WARM)
    facebook_caption = models.TextField(blank=True)
    instagram_caption = models.TextField(blank=True)
    link_url = models.URLField(blank=True)
    image_data = models.BinaryField(null=True, blank=True)
    image_content_type = models.CharField(max_length=80, blank=True)
    image_name = models.CharField(max_length=200, blank=True)
    post_to_facebook = models.BooleanField(default=True)
    post_to_instagram = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="social_posts",
    )
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.headline

    @property
    def headline(self) -> str:
        source = self.facebook_caption or self.instagram_caption or self.brief or "Untitled post"
        line = source.strip().splitlines()[0].strip()
        if len(line) > 90:
            return line[:87].rsplit(" ", 1)[0] + "…"
        return line or "Untitled post"

    @property
    def has_image(self) -> bool:
        return bool(self.image_data)

    def selected_networks(self) -> list[str]:
        networks = []
        if self.post_to_facebook:
            networks.append(SocialAccount.Network.FACEBOOK)
        if self.post_to_instagram:
            networks.append(SocialAccount.Network.INSTAGRAM)
        return networks


class SocialPublication(TimeStampedModel):
    class Status(models.TextChoices):
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"

    post = models.ForeignKey(SocialPost, on_delete=models.CASCADE, related_name="publications")
    network = models.CharField(max_length=16, choices=SocialAccount.Network.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    external_id = models.CharField(max_length=64, blank=True)
    permalink = models.URLField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["post", "network"], name="social_publication_once_per_network"),
        ]
        ordering = ["network"]

    def __str__(self) -> str:
        return f"{self.network} {self.status} for post {self.post_id}"
