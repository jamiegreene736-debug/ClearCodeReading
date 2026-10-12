"""Resumable carousel generation: one bounded image request per saved slide."""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import cast

from django.db import transaction
from django.utils import timezone

from apps.social.ai import _guard, _write_json, generate_image
from apps.social.exceptions import SocialError
from apps.social.media import normalize_photo, save_slides, set_cover
from apps.social.models import SocialPost, SocialPostSlide

logger = logging.getLogger(__name__)


def plan_carousel(
    *, subject: str, count: int, audience: str, tone: str
) -> dict[str, object]:
    if not 2 <= count <= 10:
        raise SocialError("Choose between 2 and 10 carousel slides.")
    if len(subject.strip()) < 12:
        raise SocialError("Add a short subject or caption before planning a carousel.")
    _guard(subject)
    result = _write_json(
        'Return JSON with "facebook" and "instagram" captions and a "slides" array of '
        "exactly the requested number of strings. Each slide is a distinct visual brief "
        "(12–500 characters) in a coherent photographic story about the subject. "
        "Use real objects, cheerful daylight, a consistent linen/teal/gold palette, no people, "
        "no text or logos. Explain the useful reading tips in the shared captions. "
        "Facebook caption: 20–5000 characters; Instagram: 10–2200 characters. "
        "These are photo slides, not separate posts.",
        {"subject": subject[:5000], "count": count, "audience": audience, "tone": tone},
    )
    slides = result.get("slides")
    facebook, instagram = result.get("facebook"), result.get("instagram")
    if (
        not isinstance(slides, list)
        or len(slides) != count
        or not isinstance(facebook, str)
        or not 20 <= len(facebook.strip()) <= 5000
        or not isinstance(instagram, str)
        or not 10 <= len(instagram.strip()) <= 2200
    ):
        raise SocialError("The carousel plan could not be read. Try again.")
    for slide in slides:
        if not isinstance(slide, str) or not 12 <= len(slide.strip()) <= 500:
            raise SocialError(
                "The carousel plan contained an unusable slide. Try again."
            )
        _guard(slide)
    if len(set(slides)) != count:
        raise SocialError("The carousel slides were too similar. Try again.")
    _guard(facebook)
    _guard(instagram)
    return {
        "slides": slides,
        "completed": 0,
        "facebook": facebook.strip(),
        "instagram": instagram.strip(),
    }


def generation_active(post: SocialPost) -> bool:
    return bool(post.carousel_plan.get("token"))


def generation_pending(post: SocialPost) -> bool:
    plan = post.carousel_plan
    return bool(plan and int(plan.get("completed", 0)) < len(plan.get("slides", [])))


def generate_next_slide(pk: int) -> tuple[int, int]:
    token = secrets.token_hex(16)
    with transaction.atomic():
        post = SocialPost.objects.select_for_update().filter(pk=pk).first()
        if post is None:
            raise SocialError("This draft has been deleted.")
        if post.status != SocialPost.Status.DRAFT or post.publications.exists():
            raise SocialError("Only an unpublished draft can generate carousel slides.")
        plan = dict(post.carousel_plan)
        slides = cast(list[str], plan.get("slides", []))
        completed = int(plan.get("completed", 0))
        if not slides:
            raise SocialError("Plan an AI carousel first.")
        if completed >= len(slides):
            return completed, len(slides)
        if (
            plan.get("token")
            and float(plan.get("started", 0))
            > (timezone.now() - timedelta(minutes=5)).timestamp()
        ):
            raise SocialError(
                "A slide is already being generated. Wait a moment before resuming."
            )
        plan.update(token=token, started=timezone.now().timestamp())
        post.carousel_plan = plan
        post.save(update_fields=["carousel_plan", "updated_at"])
    try:
        raw, _ = generate_image(subject=slides[completed])
        photo = normalize_photo(raw, f"ai-slide-{completed + 1}.jpg")
    except SocialError:
        with transaction.atomic():
            current = SocialPost.objects.select_for_update().filter(pk=pk).first()
            if current and current.carousel_plan.get("token") == token:
                current.carousel_plan = {**current.carousel_plan, "token": ""}
                current.save(update_fields=["carousel_plan", "updated_at"])
        raise
    with transaction.atomic():
        current = SocialPost.objects.select_for_update().filter(pk=pk).first()
        if (
            current is None
            or current.carousel_plan.get("token") != token
            or current.status != SocialPost.Status.DRAFT
        ):
            raise SocialError(
                "The draft changed during generation. Reload it before continuing."
            )
        if completed == 0:
            set_cover(current, [photo])
            save_slides(current, [photo])
        else:
            SocialPostSlide.objects.create(
                post=current,
                position=completed,
                image_data=photo.data,
                image_name=photo.name,
            )
        current.carousel_plan = {
            **current.carousel_plan,
            "completed": completed + 1,
            "token": "",
        }
        current.save()
    logger.info(
        "social_carousel_slide_generated post=%s slide=%s total=%s",
        pk,
        completed + 1,
        len(slides),
    )
    return completed + 1, len(slides)
