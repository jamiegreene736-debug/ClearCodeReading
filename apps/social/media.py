"""Validate and atomically store ordered photos without changing legacy covers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.social.exceptions import SocialError
from apps.social.models import SocialPost, SocialPostSlide

MAX_IMAGES = 10
MAX_IMAGE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Photo:
    data: bytes
    name: str
    content_type: str = "image/jpeg"


def normalize_photo(raw: bytes, name: str) -> Photo:
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise SocialError("Each photo must be under 8 MB.")
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError
            if source.width * source.height > 4096 * 4096:
                raise ValueError
            source.verify()
        with Image.open(BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            # A common square canvas preserves every slide without Instagram cropping it.
            image.thumbnail((1080, 1080))
            canvas = Image.new("RGB", (1080, 1080), "#F7F2EA")
            canvas.paste(
                image, ((1080 - image.width) // 2, (1080 - image.height) // 2), image
            )
            output = BytesIO()
            canvas.save(output, "JPEG", quality=92)
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as exc:
        raise SocialError(
            "Use a valid JPEG, PNG, or WebP photo up to 16 megapixels."
        ) from exc
    return Photo(output.getvalue(), f"{Path(name).stem[:190] or 'photo'}.jpg")


def uploaded_photo(upload: UploadedFile[bytes]) -> Photo:
    return normalize_photo(upload.read(MAX_IMAGE_BYTES + 1), upload.name or "photo")


def set_cover(post: SocialPost, photos: list[Photo]) -> None:
    cover = photos[0] if photos else None
    post.image_data = cover.data if cover else None
    post.image_name = cover.name if cover else ""
    post.image_content_type = cover.content_type if cover else ""


def save_slides(post: SocialPost, photos: list[Photo]) -> None:
    """Caller holds the post lock and saves its cover in the same transaction."""
    if len(photos) > MAX_IMAGES:
        raise SocialError("A post can have up to 10 photos.")
    post.slides.all().delete()
    SocialPostSlide.objects.bulk_create(
        [
            SocialPostSlide(
                post=post, position=index, image_data=photo.data, image_name=photo.name
            )
            for index, photo in enumerate(photos[1:], 1)
        ]
    )


def edited_photos(post: SocialPost, request: HttpRequest) -> list[Photo] | None:
    """Only accept this post's saved IDs and each uploaded file exactly once."""
    order = request.POST.get("photo_order")
    if order is None:
        uploads = request.FILES.getlist("images")
        if len(uploads) > MAX_IMAGES:
            raise SocialError("A post can have up to 10 photos.")
        return [uploaded_photo(upload) for upload in uploads] if uploads else None
    try:
        keys = json.loads(order)
        if (
            not isinstance(keys, list)
            or len(keys) > MAX_IMAGES
            or any(not isinstance(key, str) for key in keys)
        ):
            raise ValueError
        if len(set(keys)) != len(keys):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise SocialError("Choose up to 10 different photos and try again.") from exc
    saved: dict[str, Photo] = {}
    if post.has_image:
        saved["cover"] = Photo(
            bytes(post.image_data or b""), post.image_name, post.image_content_type
        )
    if post.pk:
        saved.update(
            {
                str(slide.pk): Photo(bytes(slide.image_data), slide.image_name)
                for slide in post.slides.all()
            }
        )
    uploads = request.FILES.getlist("images")
    if len(uploads) > MAX_IMAGES:
        raise SocialError("A post can have up to 10 photos.")
    expected_uploads = {f"upload:{index}" for index in range(len(uploads))}
    if {key for key in keys if key.startswith("upload:")} != expected_uploads:
        raise SocialError(
            "The selected photos changed. Select them again before saving."
        )
    if any(key not in saved and key not in expected_uploads for key in keys):
        raise SocialError(
            "A photo no longer belongs to this draft. Reload it before saving."
        )
    # Preserve bytes for ordinary caption edits; normalize all images when assembling a carousel.
    old_order = list(saved)
    if keys == old_order and not uploads:
        return None
    saved.update(
        {
            f"upload:{index}": uploaded_photo(upload)
            for index, upload in enumerate(uploads)
        }
    )
    photos = [saved[key] for key in keys]
    if len(photos) > 1:
        photos = [normalize_photo(photo.data, photo.name) for photo in photos]
    return photos
