from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.models import TimeStampedModel


class Asset(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    digest = models.CharField(max_length=64, db_index=True)
    data = models.BinaryField()
    preview = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering: ClassVar = ["-created_at"]

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")


class Topic(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering: ClassVar = ["name"]

    def __str__(self) -> str:
        return self.name


class Resource(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    slug = models.SlugField(max_length=180)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    draft = models.ForeignKey(
        "Revision", on_delete=models.PROTECT, null=True, related_name="+"
    )
    live = models.ForeignKey(
        "Revision", on_delete=models.PROTECT, null=True, related_name="+"
    )
    scheduled = models.ForeignKey(
        "Revision", on_delete=models.PROTECT, null=True, related_name="+"
    )
    publish_at = models.DateTimeField(null=True, blank=True)
    archived = models.BooleanField(default=False, db_index=True)
    submitted = models.BooleanField(default=False, db_index=True)
    version = models.PositiveIntegerField(default=0)

    class Meta:
        ordering: ClassVar = ["-updated_at"]
        permissions: ClassVar = [
            ("publish_resource", "Can review and publish website resources")
        ]
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=(
                    models.Q(scheduled__isnull=True, publish_at__isnull=True)
                    | models.Q(scheduled__isnull=False, publish_at__isnull=False)
                ),
                name="resource_schedule_has_time",
            )
        ]

    @property
    def published_revision(self) -> Revision | None:
        if self.archived:
            return None
        if self.scheduled_id and self.publish_at and self.publish_at <= timezone.now():
            return self.scheduled
        return self.live

    @property
    def state(self) -> str:
        if self.archived:
            return "Archived"
        if self.submitted:
            return "In review"
        if self.scheduled_id and self.publish_at and self.publish_at > timezone.now():
            return "Scheduled"
        return "Published" if self.published_revision else "Draft"

    @property
    def has_unpublished_changes(self) -> bool:
        revision = self.published_revision
        return bool(revision and self.draft_id != revision.pk)

    def get_absolute_url(self) -> str:
        return reverse("resources:detail", args=[self.pk, self.slug])


class Revision(models.Model):
    class Kind(models.TextChoices):
        FILE = "file", "File"
        LINK = "link", "Link or video"
        ARTICLE = "article", "Article"

    class Audience(models.TextChoices):
        FAMILIES = "families", "Families"
        EDUCATORS = "educators", "Educators"
        BOTH = "both", "Families and educators"

    class Access(models.TextChoices):
        FAMILY = "family", "Family registration required"
        PUBLIC = "public", "Everyone (public)"

    resource = models.ForeignKey(
        Resource, on_delete=models.CASCADE, related_name="revisions"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    title = models.CharField(max_length=200, default="Untitled resource")
    description = models.CharField(max_length=320, blank=True)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    audience = models.CharField(
        max_length=12, choices=Audience.choices, default=Audience.FAMILIES
    )
    topic = models.ForeignKey(Topic, on_delete=models.PROTECT, null=True, blank=True)
    body = models.TextField(blank=True, max_length=100000)
    url = models.URLField(max_length=2000, blank=True)
    asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="file_revisions",
    )
    cover = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cover_revisions",
    )
    cover_alt = models.CharField(max_length=240, blank=True)
    grade = models.CharField(max_length=80, blank=True)
    language = models.CharField(max_length=60, default="English")
    access = models.CharField(
        max_length=12, choices=Access.choices, default=Access.FAMILY
    )
    featured = models.BooleanField(default=False)
    seo_title = models.CharField(max_length=70, blank=True)
    seo_description = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering: ClassVar = ["-created_at", "-pk"]

    @property
    def is_pdf(self) -> bool:
        return bool(self.asset and self.asset.content_type == "application/pdf")

    @property
    def image_asset(self) -> Asset | None:
        if self.cover_id:
            return self.cover
        if self.asset:
            if self.asset.is_image:
                return self.asset
            if self.asset.preview_id:
                return self.asset.preview
        return None


class UploadBatch(models.Model):
    token = models.UUIDField(primary_key=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    resources = models.ManyToManyField(Resource)
    created_at = models.DateTimeField(auto_now_add=True)


class ResourceEvent(models.Model):
    resource = models.ForeignKey(
        Resource, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    action = models.CharField(max_length=32)
    revision = models.ForeignKey(Revision, on_delete=models.PROTECT, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar = ["-created_at"]

    @property
    def display_action(self) -> str:
        return self.action.replace("_", " ").capitalize()


class StaffSetupToken(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
