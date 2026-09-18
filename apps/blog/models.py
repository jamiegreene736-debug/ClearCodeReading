import math

import nh3
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.html import linebreaks
from django.utils.safestring import mark_safe
from django.utils.text import slugify

from apps.core.models import TimeStampedModel

# Tags and attributes an editor may use in a rich-text article body. Anything
# else (scripts, inline event handlers, iframes, styles) is stripped on save.
ALLOWED_BODY_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "a", "h2", "h3", "h4",
    "ul", "ol", "li", "blockquote", "hr", "img", "figure", "figcaption", "code", "pre",
}
ALLOWED_BODY_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height", "loading"},
}
ALLOWED_URL_SCHEMES = {"http", "https", "mailto", "tel"}


def clean_article_html(value: str) -> str:
    """Return ``value`` with anything outside the article whitelist removed."""
    return nh3.clean(
        value or "",
        tags=ALLOWED_BODY_TAGS,
        attributes=ALLOWED_BODY_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )


class BlogPostQuerySet(models.QuerySet):
    def published(self):
        return self.filter(
            status=BlogPost.Status.PUBLISHED,
            published_at__isnull=False,
            published_at__lte=timezone.now(),
        )

    def scheduled(self):
        return self.filter(
            status=BlogPost.Status.PUBLISHED,
            published_at__gt=timezone.now(),
        )

    def drafts(self):
        return self.filter(status=BlogPost.Status.DRAFT)


class BlogPost(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    class BodyFormat(models.TextChoices):
        PLAIN = "plain", "Plain text"
        HTML = "html", "Rich text"

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
    body_format = models.CharField(
        max_length=8,
        choices=BodyFormat.choices,
        default=BodyFormat.PLAIN,
        help_text="Rich text bodies are sanitised HTML written in the portal editor.",
    )
    category = models.CharField(max_length=80, blank=True)
    cover_image = models.ImageField(upload_to="blog/covers/%Y/%m/", blank=True)
    cover_data = models.BinaryField(null=True, blank=True, editable=False)
    cover_content_type = models.CharField(max_length=80, blank=True, editable=False)
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
        if self.has_cover and not self.cover_image_alt.strip():
            raise ValidationError(
                {"cover_image_alt": "Describe the cover image before saving the post."}
            )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._available_slug()
        if self.body_format == self.BodyFormat.HTML:
            self.body = clean_article_html(self.body)
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

    # -- Cover images -------------------------------------------------------

    @property
    def has_cover(self) -> bool:
        return bool(self.cover_data) or bool(self.cover_image)

    @property
    def cover_url(self) -> str:
        if self.cover_data:
            return reverse("blog:cover", kwargs={"slug": self.slug})
        if self.cover_image:
            return self.cover_image.url
        return ""

    def set_cover(self, uploaded_file) -> None:
        """Store an uploaded image in the database so it survives redeploys."""
        self.cover_data = uploaded_file.read()
        self.cover_content_type = uploaded_file.content_type or "image/jpeg"
        self.cover_image = None

    def clear_cover(self) -> None:
        self.cover_data = None
        self.cover_content_type = ""
        self.cover_image = None
        self.cover_image_alt = ""

    # -- Publication state --------------------------------------------------

    @property
    def is_live(self) -> bool:
        return (
            self.status == self.Status.PUBLISHED
            and self.published_at is not None
            and self.published_at <= timezone.now()
        )

    @property
    def is_scheduled(self) -> bool:
        return (
            self.status == self.Status.PUBLISHED
            and self.published_at is not None
            and self.published_at > timezone.now()
        )

    @property
    def state(self) -> str:
        if self.is_live:
            return "Published"
        if self.is_scheduled:
            return "Scheduled"
        return "Draft"

    # -- Presentation -------------------------------------------------------

    @property
    def display_author(self):
        if not self.author:
            return "ClearCode Reading"
        return self.author.get_full_name().strip() or "ClearCode Reading"

    @property
    def plain_text(self) -> str:
        if self.body_format == self.BodyFormat.HTML:
            return nh3.clean(self.body or "", tags=set())
        return self.body or ""

    @property
    def reading_time_minutes(self):
        return max(1, math.ceil(len(self.plain_text.split()) / 200))

    @property
    def rendered_body(self):
        if self.body_format == self.BodyFormat.HTML:
            return mark_safe(clean_article_html(self.body))
        return mark_safe(linebreaks(self.body or "", autoescape=True))
