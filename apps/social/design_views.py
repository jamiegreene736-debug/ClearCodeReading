"""Saved templates and editable post artwork, kept separate from publishing."""

from __future__ import annotations

from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.social.access import SocialRequest, social_editor_required
from apps.social.carousel_ai import generation_active
from apps.social.designs import raster, starter, validate_document
from apps.social.exceptions import SocialError
from apps.social.media import (
    MAX_IMAGE_BYTES,
    Photo,
    normalize_photo,
    save_slides,
    set_cover,
)
from apps.social.models import ContentPlan, ContentWeek, SocialImageTemplate, SocialPost


@social_editor_required
@require_http_methods(["GET"])
def library(request: SocialRequest) -> HttpResponse:
    return render(
        request,
        "social/design_library.html",
        {
            "templates": SocialImageTemplate.objects.filter(archived=False).defer(
                "document", "preview"
            )
        },
    )


@social_editor_required
@require_http_methods(["GET"])
def editor(request: SocialRequest, pk: int | None = None) -> HttpResponse:
    post = get_object_or_404(SocialPost, pk=pk) if pk else None
    template = None
    template_id = request.GET.get("template", "")
    if template_id:
        if not template_id.isdecimal() or len(template_id) > 18:
            return HttpResponse("Invalid template", status=400)
        template = get_object_or_404(
            SocialImageTemplate, pk=template_id, archived=False
        )
    document = (
        template.document
        if template
        else post.image_design
        if post and post.image_design
        else starter(request.GET.get("style", "scripts"))
    )
    response = render(
        request,
        "social/design_editor.html",
        {
            "post": post,
            "template": template,
            "templates": SocialImageTemplate.objects.filter(archived=False).only(
                "pk", "name"
            ),
            "studio": {
                "document": document,
                "templateId": template.pk if template else None,
                "templateRevision": template.updated_at.isoformat() if template else "",
                "postId": post.pk if post else None,
                "postRevision": post.updated_at.isoformat() if post else "",
                "saveUrl": reverse("social:design_save"),
                "applyUrl": reverse("social:design_apply"),
                "hasPhotos": bool(post and post.has_image),
            },
        },
    )
    response["Cache-Control"] = "private, no-store"
    return response


def error(exc: SocialError) -> JsonResponse:
    return JsonResponse({"error": str(exc)}, status=400)


@social_editor_required
@require_http_methods(["POST"])
def save_template(request: SocialRequest) -> HttpResponse:
    try:
        name = request.POST.get("name", "").strip()
        if not 1 <= len(name) <= 100:
            raise SocialError("Name your template using 1–100 characters.")
        document = validate_document(request.POST.get("document", ""))
        upload = request.FILES.get("preview")
        if upload is None:
            raise SocialError("The template preview is missing. Try saving again.")
        preview = raster(upload.read(MAX_IMAGE_BYTES + 1))
        with transaction.atomic():
            template_id = request.POST.get("template_id", "")
            if template_id:
                if not template_id.isdecimal() or len(template_id) > 18:
                    raise SocialError("Choose a valid template.")
                template = get_object_or_404(
                    SocialImageTemplate.objects.select_for_update(),
                    pk=template_id,
                    archived=False,
                )
                if request.POST.get("revision") != template.updated_at.isoformat():
                    raise SocialError(
                        "This template changed in another window. Reload it or save a new copy."
                    )
            else:
                template = SocialImageTemplate()
            template.name, template.document, template.preview = name, document, preview
            template.save()
        return JsonResponse(
            {
                "id": template.pk,
                "revision": template.updated_at.isoformat(),
                "name": template.name,
            }
        )
    except SocialError as exc:
        return error(exc)


@social_editor_required
@require_http_methods(["GET"])
def preview(request: SocialRequest, pk: int) -> HttpResponse:
    template = get_object_or_404(
        SocialImageTemplate.objects.only("preview"), pk=pk, archived=False
    )
    response = HttpResponse(bytes(template.preview), content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@social_editor_required
@require_http_methods(["POST"])
def archive_template(request: SocialRequest, pk: int) -> HttpResponse:
    with transaction.atomic():
        template = get_object_or_404(
            SocialImageTemplate.objects.select_for_update(), pk=pk
        )
        if request.POST.get("revision") != template.updated_at.isoformat():
            return error(
                SocialError("This template changed. Reload before removing it.")
            )
        template.archived = True
        template.save(update_fields=["archived", "updated_at"])
    from django.shortcuts import redirect

    return redirect("social:design_library")


@social_editor_required
@require_http_methods(["POST"])
def apply_design(request: SocialRequest) -> HttpResponse:
    try:
        document = validate_document(request.POST.get("document", ""))
        uploads = request.FILES.getlist("images")
        if len(uploads) != len(document["pages"]):
            raise SocialError(
                "Export all design pages before adding them to your post."
            )
        photos = [
            Photo(
                raster(upload.read(MAX_IMAGE_BYTES + 1)),
                f"design-{i + 1}.png",
                "image/png",
            )
            for i, upload in enumerate(uploads)
        ]
        with transaction.atomic():
            ContentPlan.objects.select_for_update().filter(pk=1).first()
            post_id = request.POST.get("post_id", "")
            if post_id:
                if not post_id.isdecimal() or len(post_id) > 18:
                    raise SocialError("Choose a valid post.")
                weeks = list(
                    ContentWeek.objects.select_for_update().filter(post_id=int(post_id))
                )
                post = get_object_or_404(
                    SocialPost.objects.select_for_update(), pk=post_id
                )
                if request.POST.get("revision") != post.updated_at.isoformat():
                    raise SocialError(
                        "This post changed in another window. Reload before adding artwork."
                    )
                if (
                    post.status
                    not in {SocialPost.Status.DRAFT, SocialPost.Status.ATTENTION}
                    or post.publications.filter(status="published").exists()
                ):
                    raise SocialError(
                        "Artwork can only be added to unpublished drafts. Cancel the schedule first."
                    )
                if generation_active(post):
                    raise SocialError(
                        "Wait for the current AI image to finish before adding artwork."
                    )
                if post.is_blog_promotion:
                    raise SocialError(
                        "Blog features use link cards. Create a separate social post for this artwork."
                    )
            else:
                weeks = []
                post = SocialPost(
                    created_by=request.user, source=SocialPost.Source.MANUAL
                )
            mode = request.POST.get("mode", "append")
            if mode not in {"append", "replace"}:
                raise SocialError("Choose whether to add or replace post images.")
            if mode == "append" and post.has_image:
                existing = [
                    Photo(
                        bytes(post.image_data or b""),
                        post.image_name,
                        post.image_content_type,
                    )
                ]
                existing.extend(
                    Photo(bytes(slide.image_data), slide.image_name)
                    for slide in post.slides.all()
                )
                photos = existing + photos
            if len(photos) > 10:
                raise SocialError(
                    "A post can have up to 10 images. Remove pages or choose Replace images."
                )
            if len(photos) > 1:
                photos = [normalize_photo(photo.data, photo.name) for photo in photos]
            set_cover(post, photos)
            post.image_design, post.carousel_plan = document, {}
            post.save()
            save_slides(post, photos)
            if weeks:
                ContentWeek.objects.filter(pk__in=[week.pk for week in weeks]).update(
                    status=ContentWeek.Status.SKIPPED,
                    auto_scheduled=False,
                    review_passed=False,
                    review_note="Artwork changed in the template editor. Review and schedule manually.",
                    updated_at=timezone.now(),
                )
        return JsonResponse({"url": reverse("social:edit", args=[post.pk])})
    except SocialError as exc:
        return error(exc)
