import math

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import TimeStampedModel


class BlogPostQuerySet(models.QuerySet):
    def published(self):
        return self.filter(
            status=BlogPost.Status.PUBLISHED,
            published_at__isnull=False,
            published_at__lte=timezone.now(),
        )


class BlogPost(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    title = models.CharField(max_length=200)
    slug = models.SlugField(
        max_length=220,
        unique=True,
        blank=True,
        help_text="Used in the public URL. Leave blank to generate it from the title.",
    )
    excerpt = models.CharField(
        max_length=320,
        help_text="A short summary displayed on the blog landing page.",
    )
    body = models.TextField(
        help_text="Write in plain text. Paragraph breaks will be preserved on the public article.",
    )
    category = models.CharField(max_length=80, blank=True)
    cover_image = models.ImageField(upload_to="blog/covers/%Y/%m/", blank=True)
    cover_image_alt = models.CharField(
        "cover image description",
        max_length=240,
        blank=True,
        help_text="Required when a cover image is added so the article remains accessible.",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blog_posts",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Set a future date to schedule publication.",
    )
    is_featured = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Featured posts are promoted at the top of the blog landing page.",
    )
    seo_title = models.CharField(
        max_length=70,
        blank=True,
        help_text="Optional search and social title. The article title is used by default.",
    )
    seo_description = models.CharField(
        max_length=160,
        blank=True,
        help_text="Optional search description. The excerpt is used by default.",
    )

    objects = BlogPostQuerySet.as_manager()

    class Meta:
        ordering = ["-is_featured", "-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["status", "published_at"]),
            models.Index(fields=["is_featured", "published_at"]),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.cover_image and not self.cover_image_alt.strip():
            raise ValidationError(
                {"cover_image_alt": "Describe the cover image before saving the post."}
            )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._available_slug()
        if self.status == self.Status.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def _available_slug(self):
        base_slug = slugify(self.title)[:200] or "article"
        candidate = base_slug
        suffix = 2
        while type(self).objects.exclude(pk=self.pk).filter(slug=candidate).exists():
            candidate = f"{base_slug[: 219 - len(str(suffix))]}-{suffix}"
            suffix += 1
        return candidate

    def get_absolute_url(self):
        return reverse("blog:detail", kwargs={"slug": self.slug})

    @property
    def display_author(self):
        if not self.author:
            return "ClearCode Reading"
        return self.author.get_full_name().strip() or "ClearCode Reading"

    @property
    def reading_time_minutes(self):
        return max(1, math.ceil(len(self.body.split()) / 200))
