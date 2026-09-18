"""Portal editor form for blog posts."""

from __future__ import annotations

from typing import Any

from django import forms
from django.utils import timezone

from apps.blog.models import BlogPost

MAX_COVER_BYTES = 8 * 1024 * 1024
ALLOWED_COVER_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

INPUT_CLASS = (
    "focus-ring w-full rounded-2xl border border-ink/15 bg-white px-4 py-3 text-ink "
    "placeholder:text-ink/40"
)


class BlogPostForm(forms.ModelForm):
    cover_upload = forms.ImageField(
        label="Cover image",
        required=False,
        help_text="JPEG, PNG, WebP or GIF up to 8 MB. Shown on the blog tile and article header.",
    )
    remove_cover = forms.BooleanField(label="Remove the current cover image", required=False)
    published_at = forms.DateTimeField(
        label="Publish date",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        help_text="Leave blank to publish immediately, or pick a future time to schedule it.",
    )

    class Meta:
        model = BlogPost
        fields = [
            "title",
            "slug",
            "category",
            "excerpt",
            "body_format",
            "body",
            "cover_image_alt",
            "status",
            "published_at",
            "is_featured",
            "seo_title",
            "seo_description",
        ]
        labels = {
            "slug": "URL slug",
            "excerpt": "Summary",
            "body": "Article",
            "body_format": "Editor mode",
            "is_featured": "Feature this post at the top of the blog",
            "seo_title": "Search title",
            "seo_description": "Search description",
        }
        widgets = {
            "excerpt": forms.Textarea(attrs={"rows": 3}),
            "body": forms.Textarea(attrs={"rows": 18}),
            "body_format": forms.RadioSelect,
            "status": forms.RadioSelect,
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, (forms.RadioSelect, forms.CheckboxInput)):
                continue
            css = INPUT_CLASS
            if isinstance(widget, forms.ClearableFileInput):
                css = "focus-ring block w-full text-sm text-ink/70"
            widget.attrs.setdefault("class", css)
        self.fields["title"].widget.attrs.setdefault("placeholder", "A clear, specific headline")
        self.fields["slug"].widget.attrs.setdefault("placeholder", "generated-from-the-title")
        self.fields["category"].widget.attrs.setdefault("placeholder", "For families")
        self.fields["category"].widget.attrs.setdefault("list", "blog-category-options")
        self.fields["excerpt"].widget.attrs.setdefault(
            "placeholder", "One or two sentences that make someone want to read on."
        )
        self.fields["body"].widget.attrs["data-article-body"] = "1"
        self.fields["cover_image_alt"].widget.attrs.setdefault(
            "placeholder", "A parent and child reading together on a sofa"
        )
        self.fields["seo_title"].widget.attrs.setdefault("maxlength", "70")
        self.fields["seo_description"].widget.attrs.setdefault("maxlength", "160")

    def clean_cover_upload(self):
        upload = self.cleaned_data.get("cover_upload")
        if not upload:
            return upload
        if upload.size > MAX_COVER_BYTES:
            raise forms.ValidationError("Choose an image smaller than 8 MB.")
        content_type = getattr(upload, "content_type", "") or ""
        if content_type not in ALLOWED_COVER_TYPES:
            raise forms.ValidationError("Upload a JPEG, PNG, WebP or GIF image.")
        return upload

    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get("cover_upload")
        remove = cleaned.get("remove_cover")
        will_have_cover = bool(upload) or (self.instance.has_cover and not remove)
        if will_have_cover and not (cleaned.get("cover_image_alt") or "").strip():
            self.add_error(
                "cover_image_alt",
                "Describe the cover image so the article stays accessible.",
            )
        if cleaned.get("status") == BlogPost.Status.PUBLISHED and not cleaned.get("published_at"):
            cleaned["published_at"] = timezone.now()
        return cleaned

    def save(self, commit: bool = True) -> BlogPost:
        post: BlogPost = super().save(commit=False)
        if self.cleaned_data.get("remove_cover"):
            post.clear_cover()
        upload = self.cleaned_data.get("cover_upload")
        if upload:
            post.set_cover(upload)
        if commit:
            post.save()
        return post
