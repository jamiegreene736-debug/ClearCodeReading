import base64
import binascii
import math
import re
import uuid

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

# Inline article images. Anything larger is rejected at upload and on save.
MAX_INLINE_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_INLINE_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

_DATA_IMAGE_SRC = re.compile(
    r"""(<img\b[^>]*?\bsrc\s*=\s*)(["'])data:(image/[a-z0-9.+-]+);base64,([^"']*)\2""",
    re.IGNORECASE,
)
_IMG_SRC = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


def clean_article_html(value: str) -> str:
    """Return ``value`` with anything outside the article whitelist removed."""
    return nh3.clean(
        value or "",
        tags=ALLOWED_BODY_TAGS,
        attributes=ALLOWED_BODY_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )


class BlogImage(TimeStampedModel):
    """An image pasted or uploaded into an article body.

    Bytes live in the database (like post covers) so they survive redeploys on
    hosts without persistent disk. The public URL uses an unguessable key so
    images attached to drafts are not enumerable.
    """

    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    post = models.ForeignKey(
        "blog.BlogPost",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="images",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blog_images",
    )
    data = models.BinaryField(editable=False)
    content_type = models.CharField(max_length=80)
    original_name = models.CharField(max_length=200, blank=True)
    size = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name or str(self.key)

    def get_absolute_url(self):
        return reverse("blog:image", kwargs={"key": self.key})

    @property
    def url(self) -> str:
        return self.get_absolute_url()

    @classmethod
    def create_from_bytes(cls, data: bytes, content_type: str, *, post=None, uploaded_by=None, original_name: str = "") -> "BlogImage":
        return cls.objects.create(
            data=data,
            content_type=content_type,
            post=post,
            uploaded_by=uploaded_by,
            original_name=original_name[:200],
            size=len(data),
        )


class InlineImageImportError(ValueError):
    """Raised when a pasted image could not be stored."""


def import_inline_data_images(html: str, *, post=None, uploaded_by=None) -> tuple[str, int]:
    """Replace ``data:image/...;base64`` sources with stored :class:`BlogImage` URLs.

    Editors who paste from Google Docs, Word or email bring images along as
    inline base64 data. The sanitiser would otherwise silently drop those
    sources. Returns the rewritten HTML and the number of images imported.
    """
    imported = 0

    def _store(match: re.Match) -> str:
        nonlocal imported
        content_type = match.group(3).lower()
        if content_type == "image/jpg":
            content_type = "image/jpeg"
        if content_type not in ALLOWED_INLINE_IMAGE_TYPES:
            raise InlineImageImportError(f"{content_type} images are not supported.")
        try:
            data = base64.b64decode(match.group(4), validate=False)
        except (binascii.Error, ValueError) as exc:
            raise InlineImageImportError("A pasted image was corrupted.") from exc
        if not data:
            raise InlineImageImportError("A pasted image was empty.")
        if len(data) > MAX_INLINE_IMAGE_BYTES:
            raise InlineImageImportError("A pasted image is larger than 8 MB.")
        image = BlogImage.create_from_bytes(data, content_type, post=post, uploaded_by=uploaded_by)
        imported += 1
        return f'{match.group(1)}"{image.url}"'

    return _DATA_IMAGE_SRC.sub(_store, html or ""), imported


def unsupported_inline_image_sources(html: str) -> list[str]:
    """Return image sources the sanitiser will drop (``file:``, ``blob:``, missing…)."""
    bad: list[str] = []
    for tag in _IMG_TAG.findall(html or ""):
        src_match = _IMG_SRC.search(tag)
        src = (src_match.group(1) if src_match else "").strip()
        scheme = src.split(":", 1)[0].lower() if ":" in src else ""
        if not src:
            bad.append("(no source)")
        elif scheme and scheme not in {"http", "https"}:
            bad.append(src[:60])
    return bad


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
            # Safety net for every save path (admin, shell, form): store pasted
            # base64 images before the sanitiser can throw their sources away.
            self.body, _ = import_inline_data_images(self.body, post=self if self.pk else None, uploaded_by=self.author)
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
