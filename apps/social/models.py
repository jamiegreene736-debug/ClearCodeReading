from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.db import models
from django.core.validators import RegexValidator
from django.db.models.functions import Lower

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
    insights_retry_after = models.DateTimeField(null=True, blank=True)
    insights_error = models.CharField(max_length=300, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["network"]

    def __str__(self) -> str:
        return self.display_name or self.get_network_display()

    @property
    def is_connected(self) -> bool:
        return self.status == self.Status.CONNECTED and bool(self.encrypted_token)


class SocialCategory(TimeStampedModel):
    name = models.CharField(max_length=80)
    color = models.CharField(max_length=7, default="#197B80", validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Choose a valid color.")])
    archived = models.BooleanField(default=False)
    system_key = models.CharField(max_length=30, null=True, blank=True, unique=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="social_category_name_unique")]

    def __str__(self) -> str:
        return self.name


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
        BLOG = "blog", "Blog feature"

    class Audience(models.TextChoices):
        FAMILIES = "families", "Families"
        TEACHERS = "teachers", "Teachers"
        GENERAL = "general", "General"

    class Tone(models.TextChoices):
        WARM = "warm", "Warm"
        PRACTICAL = "practical", "Practical"
        CELEBRATORY = "celebratory", "Celebratory"

    image_design = models.JSONField(default=dict, blank=True)
    category = models.ForeignKey(SocialCategory, null=True, blank=True, on_delete=models.PROTECT, related_name="posts")

    blog_post = models.OneToOneField("blog.BlogPost", on_delete=models.SET_NULL, null=True, blank=True, related_name="facebook_promotion")
    blog_auto_scheduled = models.BooleanField(default=False)
    blog_publication_at = models.DateTimeField(null=True, blank=True)
    brief = models.TextField(blank=True)
    audience = models.CharField(max_length=16, choices=Audience.choices, default=Audience.FAMILIES)
    tone = models.CharField(max_length=16, choices=Tone.choices, default=Tone.WARM)
    facebook_caption = models.TextField(blank=True)
    instagram_caption = models.TextField(blank=True)
    link_url = models.URLField(blank=True)
    carousel_plan = models.JSONField(default=dict, blank=True)
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
    def posted_at(self):
        dates = [publication.published_at for publication in self.publications.all() if publication.published_at]
        return min(dates) if dates else self.updated_at

    @property
    def has_image(self) -> bool:
        return bool(self.image_data)

    @property
    def is_blog_promotion(self) -> bool:
        return self.blog_post_id is not None or self.source == self.Source.BLOG

    def selected_networks(self) -> list[str]:
        networks = []
        if self.post_to_facebook:
            networks.append(SocialAccount.Network.FACEBOOK)
        if self.post_to_instagram:
            networks.append(SocialAccount.Network.INSTAGRAM)
        return networks


class SocialPostSlide(TimeStampedModel):
    """Additional photos after the existing cover image, in publishing order."""

    post = models.ForeignKey(
        SocialPost, on_delete=models.CASCADE, related_name="slides"
    )
    position = models.PositiveSmallIntegerField()
    image_data = models.BinaryField()
    image_name = models.CharField(max_length=200)

    class Meta:
        ordering: ClassVar[list[str]] = ["position", "pk"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["post", "position"], name="social_slide_position"
            ),
            models.CheckConstraint(
                condition=models.Q(position__gte=1, position__lte=9),
                name="social_slide_range",
            ),
        ]


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


class ContentPlan(TimeStampedModel):
    """The public brand's single weekly editorial plan."""

    class Mode(models.TextChoices):
        PAUSED = "paused", "Paused"
        REVIEW = "review", "Review first"
        AUTOMATIC = "automatic", "Automatic"

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.PAUSED)
    weekday = models.PositiveSmallIntegerField(default=1)
    posting_hour = models.PositiveSmallIntegerField(default=10)
    audience = models.CharField(
        max_length=20,
        choices=SocialPost.Audience.choices,
        default=SocialPost.Audience.FAMILIES,
    )
    priorities = models.TextField(blank=True, max_length=2000)
    post_to_facebook = models.BooleanField(default=True)
    post_to_instagram = models.BooleanField(default=True)
    preview_requested = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    last_worker_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(pk=1), name="social_single_content_plan"
            ),
            models.CheckConstraint(
                condition=models.Q(weekday__lte=6), name="social_plan_weekday"
            ),
            models.CheckConstraint(
                condition=models.Q(posting_hour__gte=8, posting_hour__lte=20),
                name="social_plan_daytime",
            ),
        ]


class ContentWeek(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Preparing"
        GENERATING = "generating", "Generating"
        READY = "ready", "Ready"
        HELD = "held", "Needs review"
        ERROR = "error", "Generation failed"
        SKIPPED = "skipped", "Skipped"

    week_of = models.DateField(unique=True)
    planned_at = models.DateTimeField()
    pillar = models.CharField(max_length=40)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    post = models.OneToOneField(
        SocialPost,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="content_week",
    )
    context = models.JSONField(default=dict)
    content = models.JSONField(default=dict)
    auto_scheduled = models.BooleanField(default=False)
    review_passed = models.BooleanField(default=False)
    review_note = models.CharField(max_length=500, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["planned_at"]


class PublicationInsights(TimeStampedModel):
    publication = models.OneToOneField(SocialPublication, on_delete=models.CASCADE, related_name="insights")
    metrics = models.JSONField(default=dict)
    metric_times = models.JSONField(default=dict)
    unavailable = models.JSONField(default=dict)
    checked_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    next_sync_at = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)


class InsightSnapshot(models.Model):
    publication = models.ForeignKey(SocialPublication, on_delete=models.CASCADE, related_name="insight_snapshots")
    day = models.DateField()
    captured_at = models.DateTimeField()
    metrics = models.JSONField(default=dict)

    class Meta:
        ordering: ClassVar[list[str]] = ["captured_at"]
        constraints = [models.UniqueConstraint(fields=["publication", "day"], name="social_insight_daily_unique")]


class SocialImageTemplate(TimeStampedModel):
    name = models.CharField(max_length=100)
    document = models.JSONField(default=dict)
    preview = models.BinaryField()
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-updated_at", "pk"]
