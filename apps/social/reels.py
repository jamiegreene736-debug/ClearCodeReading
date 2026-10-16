"""Reviewable moving-video stories and bounded, resumable provider requests."""

from __future__ import annotations

import logging
import secrets
from typing import Any

import requests
from django.db import transaction
from django.utils import timezone

from apps.social.ai import _guard, _write_json
from apps.social.exceptions import SocialError
from apps.social.models import SocialPost
from apps.social.veo import (
    DURATION,
    VideoRejected,
    download_video,
    model,
    poll_video,
    require_key,
    start_video,
)

logger = logging.getLogger(__name__)
ACTIVE = {"submitting", "queued", "in_progress", "downloading", "uncertain"}


def editable(post: SocialPost) -> None:
    if post.status != SocialPost.Status.DRAFT or (
        post.pk and post.publications.exists()
    ):
        raise SocialError("Only an unpublished draft can generate or change a Reel.")
    if post.reel_job.get("status") in ACTIVE:
        raise SocialError(
            "This video is still generating. Check its progress before editing."
        )


def clean_prompt(value: str) -> str:
    value = value.strip()
    if not 30 <= len(value) <= 6000:
        raise SocialError("Use 30–6,000 characters for your video storyboard.")
    _guard(value)
    return value


def plan_reel(*, subject: str, audience: str, tone: str) -> dict[str, str]:
    if not 12 <= len(subject.strip()) <= 5000:
        raise SocialError("Describe your Reel in 12–5,000 characters.")
    _guard(subject)
    result = _write_json(
        'Return JSON strings "storyboard", "facebook", and "instagram". Create a '
        "creative 8-second vertical live-action Reel: 0–2 seconds a striking visual hook, "
        "2–5 seconds a meaningful action or change, 5–8 seconds a satisfying practical "
        "payoff. Storyboard: 30–6000 characters with timed shots, visible motion, camera "
        "direction, natural sound and optional short spoken dialogue. Keep it filmable, "
        "with at most three shots and consistent fictional characters. Preserve specific "
        "props and emotions. A child disliking a tablet must visibly have a tablet and "
        "look disengaged before seeking an adult with a book. Do not repeat three generic "
        "teaching scenes. Vary wide/medium/detail framing and represent diverse families "
        "without stereotypes. Natural respectful emotions, including boredom, are allowed. "
        "No shaming, miracle transformation, invented testimonials, logos, copyrighted "
        "music, real people, or on-screen text. Describe a real moving scene, not a photo "
        "slideshow. Put useful reading advice and a gentle CTA in the captions. Facebook "
        "20–5000 characters; Instagram 10–2200 characters.",
        {"subject": subject, "audience": audience, "tone": tone},
    )
    output: dict[str, str] = {}
    for key, minimum, maximum in (
        ("storyboard", 30, 6000),
        ("facebook", 20, 5000),
        ("instagram", 10, 2200),
    ):
        value = result.get(key)
        if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
            raise SocialError("The Reel plan could not be read. Try planning again.")
        _guard(value)
        output[key] = value.strip()
    return output


def start_reel(pk: int) -> None:
    require_key()
    selected_model = model()
    token = secrets.token_hex(16)
    with transaction.atomic():
        post = SocialPost.objects.select_for_update().get(pk=pk)
        editable(post)
        prompt = clean_prompt(post.reel_prompt)
        if not post.is_reel:
            raise SocialError("Choose a Reel draft first.")
        if post.video_data:
            raise SocialError(
                "Save a revised storyboard before generating a replacement video."
            )
        post.reel_job = {"status": "submitting", "token": token}
        post.save(update_fields=["reel_job", "updated_at"])
    # Never retry a paid create request after an ambiguous network response.
    try:
        job_id = start_video(
            "Create a cinematic, photorealistic 9:16 moving story for ClearCode Reading. "
            "Use fictional fully clothed characters, believable motion, candid expressions, "
            "natural daylight and clear sound. Fit the story into 8 seconds, preserving "
            "requested objects and emotions. No real people, copyrighted music, logos, "
            "text overlays, medical claims or promised outcomes. Storyboard:\n" + prompt
        )
    except VideoRejected as exc:
        SocialPost.objects.filter(pk=pk, reel_job__token=token).update(
            reel_job={"status": "failed", "error": str(exc)}, updated_at=timezone.now()
        )
        raise
    except SocialError:
        SocialPost.objects.filter(pk=pk, reel_job__token=token).update(
            reel_job={"status": "uncertain", "token": token}, updated_at=timezone.now()
        )
        raise SocialError(
            "Video submission was not confirmed. To avoid a second charge, generation is held. Ask an administrator to reconcile this request with the AI provider."
        ) from None
    SocialPost.objects.filter(pk=pk, reel_job__token=token).update(
        reel_job={
            "status": "queued",
            "id": job_id,
            "checked": 0,
            "provider": "veo",
            "model": selected_model,
            "seconds": DURATION,
        },
        updated_at=timezone.now(),
    )
    logger.info("social_video_started post=%s job=%s", pk, job_id)


def refresh_reel(pk: int) -> dict[str, Any]:
    with transaction.atomic():
        post = SocialPost.objects.select_for_update().get(pk=pk)
        job = dict(post.reel_job)
        if job.get("status") not in {"queued", "in_progress", "downloading"}:
            return job
        now = timezone.now().timestamp()
        if now - float(job.get("checked", 0)) < 15:
            return job
        if job.get("provider") != "veo":
            job.update(
                status="failed",
                error="This draft used the retired Sora service. Your storyboard is saved; connect Google Veo to generate it again.",
            )
            post.reel_job = job
            post.save(update_fields=["reel_job", "updated_at"])
            return job
        job_id = str(job.get("id", ""))
        job["checked"] = now
        post.reel_job = job
        post.save(update_fields=["reel_job", "updated_at"])
    result = poll_video(job_id)
    status = result.get("status")
    if status not in {"queued", "in_progress", "completed", "failed"}:
        raise SocialError(
            "The video service returned an unknown status. Check again later."
        )
    job["status"] = status
    if result.get("error"):
        job["error"] = result["error"]
    progress = result.get("progress", 0)
    job["progress"] = (
        max(0, min(100, progress)) if isinstance(progress, (int, float)) else 0
    )
    video = None
    if status == "completed":
        # A failed download can be resumed without starting or paying for another render.
        job["status"] = "downloading"
        SocialPost.objects.filter(pk=pk, reel_job__id=job_id).update(reel_job=job)
        try:
            video = download_video(str(result.get("uri", "")))
        except requests.RequestException as exc:
            raise SocialError(
                "The video download stopped. Check progress again to resume."
            ) from exc
        job["status"] = "completed"
    with transaction.atomic():
        current = SocialPost.objects.select_for_update().get(pk=pk)
        if current.reel_job.get("id") != job_id:
            raise SocialError("This draft changed. Reload before continuing.")
        current.reel_job = job
        if video is not None:
            current.video_data = video
        current.save(update_fields=["reel_job", "video_data", "updated_at"])
    logger.info("social_video_progress post=%s status=%s", pk, job["status"])
    return job
