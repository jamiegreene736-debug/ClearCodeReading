"""Reel storyboard, generation controls, and signed MP4 delivery."""

from __future__ import annotations

import re

from django.contrib import messages
from django.core.signing import BadSignature, TimestampSigner
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from apps.social.access import SocialRequest, can_manage_social, social_editor_required
from apps.social.ai import ai_configured
from apps.social.exceptions import SocialError
from apps.social.models import SocialPost
from apps.social.navigation import navigation
from apps.social.reels import (
    clean_prompt,
    editable,
    plan_reel,
    refresh_reel,
    start_reel,
)


@social_editor_required
def editor(request: SocialRequest, pk: int | None = None) -> HttpResponse:
    post = (
        get_object_or_404(SocialPost, pk=pk, is_reel=True)
        if pk
        else SocialPost(is_reel=True)
    )
    if post.status in {SocialPost.Status.POSTED, SocialPost.Status.PUBLISHING}:
        return redirect("social:post_details", pk=post.pk)
    if request.method == "POST":
        try:
            with transaction.atomic():
                if pk:
                    post = SocialPost.objects.select_for_update().get(pk=pk)
                    if (
                        parse_datetime(request.POST.get("revision", ""))
                        != post.updated_at
                    ):
                        raise SocialError("This draft changed. Reload before saving.")
                editable(post)
                brief = request.POST.get("brief", "").strip()
                if not 12 <= len(brief) <= 5000:
                    raise SocialError("Describe your Reel in 12–5,000 characters.")
                post.brief = brief
                post.post_to_facebook = request.POST.get("post_to_facebook") == "on"
                post.post_to_instagram = request.POST.get("post_to_instagram") == "on"
                action = request.POST.get("action", "save")
                if action == "plan":
                    plan = plan_reel(
                        subject=brief, audience=post.audience, tone=post.tone
                    )
                    prompt = plan["storyboard"]
                    facebook, instagram = plan["facebook"], plan["instagram"]
                else:
                    prompt = clean_prompt(request.POST.get("reel_prompt", ""))
                    facebook = request.POST.get("facebook_caption", "").strip()
                    instagram = request.POST.get("instagram_caption", "").strip()
                if len(facebook) > 5000 or len(instagram) > 2200:
                    raise SocialError(
                        "Shorten the captions: Facebook allows 5,000 characters; Instagram 2,200."
                    )
                if prompt != post.reel_prompt:
                    post.reel_job = {}
                    post.video_data = None
                post.reel_prompt = prompt
                post.facebook_caption, post.instagram_caption = facebook, instagram
                if not pk:
                    post.created_by = request.user
                post.save()
            messages.success(
                request,
                "Storyboard and captions saved. Review them before generating your video.",
            )
            return redirect("social:reel_edit", pk=post.pk)
        except (SocialError, ValueError) as exc:
            messages.error(
                request,
                str(exc)
                if isinstance(exc, SocialError)
                else "Reload this draft before saving.",
            )
    return render(
        request,
        "social/reel_form.html",
        {
            "post": post,
            "nav": navigation(request, post),
            "ai_ready": ai_configured(),
            "busy": post.reel_job.get("status")
            in {"submitting", "queued", "in_progress", "downloading", "uncertain"},
        },
    )


@social_editor_required
@require_POST
def generate(request: HttpRequest, pk: int) -> JsonResponse:
    get_object_or_404(SocialPost, pk=pk, is_reel=True)
    try:
        if request.POST.get("action") == "start":
            start_reel(pk)
        elif request.POST.get("action") != "refresh":
            raise SocialError("Choose Generate video or Check progress.")
        job = refresh_reel(pk)
    except SocialError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(
        {"status": job.get("status", ""), "progress": job.get("progress", 0)}
    )


def video_url(post: SocialPost) -> str:
    from apps.social.meta import public_base_url

    base = public_base_url()
    if not base.startswith("https://"):
        raise SocialError(
            "Set an HTTPS SOCIAL_PUBLIC_BASE_URL before publishing a Reel."
        )
    signature = TimestampSigner(salt="social-reel-video").sign(str(post.pk))
    from urllib.parse import urlencode

    return (
        base
        + reverse("social:video", args=[post.pk])
        + "?"
        + urlencode({"t": signature})
    )


def video(request: HttpRequest, pk: int) -> HttpResponse:
    signature = request.GET.get("t", "")
    if signature:
        try:
            if TimestampSigner(salt="social-reel-video").unsign(
                signature, max_age=86400
            ) != str(pk):
                raise Http404
        except BadSignature as exc:
            raise Http404 from exc
    elif not can_manage_social(request.user):
        raise Http404
    post = get_object_or_404(SocialPost, pk=pk, is_reel=True)
    if not post.video_data:
        raise Http404
    data = bytes(post.video_data)
    start, end, status = 0, len(data) - 1, 200
    value = request.headers.get("Range")
    if value:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
        if len(value) > 100 or not match or not any(match.groups()):
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{len(data)}"
            return response
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last), end) if last else end
        else:
            start = max(0, len(data) - int(last))
        if start > end or start >= len(data):
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{len(data)}"
            return response
        status = 206
    response = HttpResponse(
        data[start : end + 1], status=status, content_type="video/mp4"
    )
    response["Content-Length"] = str(end - start + 1)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = f'inline; filename="clearcode-reel-{pk}.mp4"'
    if status == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
    return response
