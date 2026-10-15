from __future__ import annotations

import calendar
import secrets
from datetime import date, timedelta

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods, require_POST

from apps.social.categories import apply_category, category_choices
from apps.social.access import can_manage_social, social_editor_required
from apps.social.ai import (
    ai_configured,
    generate_image,
    image_model,
    suggest_ideas,
    text_model,
    write_captions,
)
from apps.social.carousel_ai import (
    generate_next_slide,
    generation_active,
    generation_pending,
    plan_carousel,
)
from apps.social.crypto import decrypt_json, digest, encrypt_json
from apps.social.drafts import draft_captions
from apps.social.exceptions import SocialError
from apps.social.prompts import carousel_prompts, image_prompt_text, update_prompts
from apps.social.media import edited_photos, save_slides, set_cover
from apps.social.meta import (
    exchange_facebook_code,
    exchange_instagram_code,
    facebook_authorization_url,
    instagram_authorization_url,
    meta_ready,
    signature_matches,
)
from apps.social.models import (
    ContentPlan,
    ContentWeek,
    SocialAccount,
    SocialAuthorization,
    SocialPost,
    SocialPostSlide,
)
from apps.social.navigation import navigation
from apps.social.schedule_views import month_start
from apps.social.services import (
    EASTERN,
    cancel_schedule,
    claim_and_publish_now,
    connected_account,
    disconnect_network,
    parse_eastern,
    publish_post,
    quick_times,
    require_post_content,
    save_facebook_page,
    save_instagram_account,
    schedule_post,
    store_image,
)

TABS = (
    ("scheduled", "Scheduled"),
    ("posted", "Posted"),
    ("drafts", "Drafts"),
    ("attention", "Needs attention"),
)


def _counts() -> dict[str, int]:
    return {
        "scheduled": SocialPost.objects.filter(status=SocialPost.Status.SCHEDULED).count(),
        "posted": SocialPost.objects.filter(status=SocialPost.Status.POSTED).count(),
        "drafts": SocialPost.objects.filter(status=SocialPost.Status.DRAFT).count(),
        "attention": SocialPost.objects.filter(status=SocialPost.Status.ATTENTION).count(),
    }


def _accounts() -> dict[str, SocialAccount | None]:
    rows = {item.network: item for item in SocialAccount.objects.all()}
    return {
        "facebook": rows.get(SocialAccount.Network.FACEBOOK),
        "instagram": rows.get(SocialAccount.Network.INSTAGRAM),
    }


def _choice(value: str, allowed: set[str], default: str) -> str:
    return value if value in allowed else default


def _clean_link(value: str) -> str:
    link = (value or "").strip()
    if not link:
        return ""
    if not link.startswith(("http://", "https://")):
        raise SocialError("The link needs to start with https://.")
    return link[:200]


def _apply_post(post: SocialPost, request) -> str:
    mode = request.POST.get("mode") or post.source or SocialPost.Source.MANUAL
    if mode not in {SocialPost.Source.BRIEF, SocialPost.Source.MANUAL}:
        mode = SocialPost.Source.MANUAL
    post.source = SocialPost.Source.BLOG if post.is_blog_promotion else mode
    post.link_url = _clean_link(request.POST.get("link_url", ""))
    post.post_to_facebook = request.POST.get("post_to_facebook") == "on"
    post.post_to_instagram = request.POST.get("post_to_instagram") == "on"
    if mode == SocialPost.Source.BRIEF:
        post.brief = (request.POST.get("brief") or "")[:5000]
        post.audience = _choice(request.POST.get("audience", ""), set(SocialPost.Audience.values), post.audience)
        post.tone = _choice(request.POST.get("tone", ""), set(SocialPost.Tone.values), post.tone)
        if "facebook_caption" in request.POST:
            post.facebook_caption = (request.POST.get("facebook_caption") or "")[:5000]
            post.instagram_caption = (request.POST.get("instagram_caption") or "")[:2200]
    else:
        caption = (request.POST.get("caption") or "")[:5000]
        separate = (request.POST.get("instagram_caption") or "").strip()
        post.facebook_caption = caption
        post.instagram_caption = (separate or caption)[:2200]
    return mode


def _month(focus, current, selected):
    weeks = []
    for week in calendar.Calendar(firstweekday=calendar.SUNDAY).monthdatescalendar(focus.year, focus.month):
        weeks.append(
            [
                {
                    "day": day,
                    "in_month": day.month == focus.month,
                    "is_current": current == day,
                    "is_selected": selected == day,
                }
                for day in week
            ]
        )
    return weeks


@social_editor_required
def queue(request):
    tab = request.GET.get("tab") or "scheduled"
    if tab not in {key for key, _label in TABS}:
        tab = "scheduled"
    posts = SocialPost.objects.all()
    if tab == "scheduled":
        posts = posts.filter(status=SocialPost.Status.SCHEDULED).order_by("scheduled_at")
    elif tab == "posted":
        posts = posts.filter(status=SocialPost.Status.POSTED).order_by("-updated_at")
    elif tab == "drafts":
        posts = posts.filter(status=SocialPost.Status.DRAFT).order_by("-updated_at")
    else:
        posts = posts.filter(status=SocialPost.Status.ATTENTION).order_by("-updated_at")
    posts = posts.select_related("blog_post", "category").prefetch_related("publications")
    return render(
        request,
        "social/queue.html",
        {"tab": tab, "tabs": TABS, "posts": posts, "counts": _counts(), "accounts": _accounts()},
    )


@social_editor_required
@require_http_methods(["GET", "POST"])
def post_edit(request, pk=None):
    post = SocialPost() if pk is None else get_object_or_404(SocialPost, pk=pk)
    if pk and post.status in {SocialPost.Status.POSTED, SocialPost.Status.PUBLISHING}:
        messages.error(request, "A post that is already sending or posted cannot be edited here.")
        return redirect("social:queue")
    mode = request.GET.get("mode") or (SocialPost.Source.MANUAL if post.is_blog_promotion else post.source if pk else SocialPost.Source.BRIEF)
    nav = navigation(request, post)
    ideas: list[str] = []
    generate_image_checked = request.method != "POST" or request.POST.get("generate_image") == "on"
    generate_carousel_checked = request.POST.get("generate_carousel") == "on" if request.method == "POST" else bool(post.carousel_plan)
    if request.method == "POST":
        try:
            revision = request.POST.get("revision")
            if post.pk and revision:
                try:
                    revision_matches = parse_datetime(revision) == post.updated_at
                except ValueError:
                    revision_matches = False
                if not revision_matches:
                    raise SocialError("This draft changed in another window. Reload it before saving.")
            if generation_active(post):
                raise SocialError("A carousel slide is being generated. Finish or resume generation before editing.")
            photos = edited_photos(post, request)
            if photos is not None:
                set_cover(post, photos)
                post.carousel_plan = {}
            elif request.FILES.get("image"):
                store_image(post, request.FILES.get("image"))
                photos = []  # The legacy single-photo input replaces the whole set.
                post.carousel_plan = {}
            mode = _apply_post(post, request)
            if "image_prompt" in request.POST:
                post.image_prompt = image_prompt_text(request.POST.get("image_prompt", ""))
            action = request.POST.get("action") or "save"
            selected_idea = request.POST.get("selected_idea", "").strip()
            if selected_idea:
                if not 12 <= len(selected_idea) <= 500:
                    raise SocialError("Choose a valid idea or generate new ideas.")
                post.brief = selected_idea
                action = "draft_ai"
            if action in {"ideas", "draft_ai", "new_image", "plan_carousel"} and post.status == SocialPost.Status.SCHEDULED:
                raise SocialError("Cancel this post’s schedule before generating new content for review.")
            uploaded_images = bool(request.FILES.getlist("images") or request.FILES.get("image"))
            custom_prompts = None
            if not uploaded_images and photos is None and action not in {"discard_carousel", "new_image"}:
                custom_prompts = carousel_prompts(request.POST.getlist("carousel_prompt"), request.POST.get("slide_count", str(len(post.carousel_plan.get("slides", [])) or 3)))
            prompts_changed = False
            if custom_prompts:
                prompts_changed = update_prompts(post, custom_prompts)
            if action == "save_prompts" and not custom_prompts and not post.image_prompt:
                raise SocialError("Enter an image prompt or prompts for your carousel slides first.")
            auto_generate = action == "draft_ai" and generate_carousel_checked and not uploaded_images
            if auto_generate:
                action = "plan_carousel"
            notice = "Image prompts saved. Current photos are kept until you generate replacements." if action == "save_prompts" or prompts_changed else "Draft saved."
            if action == "plan_carousel":
                if post.is_blog_promotion:
                    raise SocialError("Blog features publish a link card. Create a separate social post for a carousel.")
                if post.status != SocialPost.Status.DRAFT or (post.pk and post.publications.exists()):
                    raise SocialError("Only an unpublished draft can generate a carousel.")
                try:
                    count = int(request.POST.get("slide_count", "3"))
                except ValueError as exc:
                    raise SocialError("Choose between 2 and 10 slides.") from exc
                if custom_prompts:
                    plan = {"slides": custom_prompts, "completed": 0}
                    if auto_generate:
                        post.facebook_caption, post.instagram_caption = write_captions(subject=post.brief, audience=post.audience, tone=post.tone, link=post.link_url)
                else:
                    plan = plan_carousel(subject=post.brief or post.facebook_caption, count=count, audience=post.audience, tone=post.tone)
                    post.facebook_caption = str(plan.pop("facebook"))
                    post.instagram_caption = str(plan.pop("instagram"))
                if auto_generate and post.link_url and post.link_url not in post.facebook_caption:
                    post.facebook_caption += f"\n\n{post.link_url}"
                post.carousel_plan = plan
                notice = "Carousel planned. Review the slide ideas, then choose Generate slides."
            elif action == "discard_carousel":
                post.carousel_plan = {}
                notice = "AI plan removed. Your current photos are kept."
            elif action == "ideas":
                ideas = suggest_ideas(audience=post.audience, tone=post.tone)
            elif action == "draft":
                facebook, instagram = draft_captions(
                    brief=post.brief,
                    audience=post.audience,
                    tone=post.tone,
                    link=post.link_url,
                )
                post.facebook_caption = facebook
                post.instagram_caption = instagram
                post.source = SocialPost.Source.BLOG if post.is_blog_promotion else SocialPost.Source.BRIEF
                notice = "Captions drafted from your words. Read them before posting."
            elif action in {"draft_ai", "new_image"}:
                if action == "draft_ai":
                    facebook, instagram = write_captions(
                        subject=post.brief,
                        audience=post.audience,
                        tone=post.tone,
                        link=post.link_url,
                    )
                    post.facebook_caption = facebook
                    post.instagram_caption = instagram
                    post.source = SocialPost.Source.BLOG if post.is_blog_promotion else SocialPost.Source.BRIEF
                    notice = "Drafted with AI. Read both captions before you schedule or post."
                if action == "new_image" or (generate_image_checked and not post.has_image):
                    try:
                        image_context = [post.facebook_caption]
                        if post.instagram_caption != post.facebook_caption:
                            image_context.append(post.instagram_caption)
                        if post.brief:
                            image_context.append(f"Supporting brief: {post.brief}")
                        post.image_prompt = post.image_prompt or "\n".join(filter(None, image_context))[:4000]
                        raw, content_type = generate_image(subject=post.image_prompt)
                    except SocialError as exc:
                        if action == "new_image" or not post.facebook_caption:
                            raise
                        notice = f"{notice} The image was not created: {exc}"
                    else:
                        post.image_data = raw
                        post.image_content_type = content_type
                        post.image_name = "ai-draft.jpg"
                        photos = []
                        post.carousel_plan = {}
                        if action == "new_image":
                            notice = "A new image is attached. The captions are unchanged."
            if action in {"schedule", "post_now"} or post.status == SocialPost.Status.SCHEDULED:
                require_post_content(post)
            if action != "ideas":
                if post.created_by_id is None:
                    post.created_by = request.user
                if post.status not in {SocialPost.Status.SCHEDULED, SocialPost.Status.ATTENTION}:
                    post.status = SocialPost.Status.DRAFT
                changing_media = photos is not None or action == "plan_carousel" or prompts_changed
                with transaction.atomic():
                    weeks = []
                    if changing_media and post.pk:
                        # Match planner lock order before retiring its old editorial approval.
                        ContentPlan.objects.select_for_update().filter(pk=1).first()
                        weeks = list(ContentWeek.objects.select_for_update().filter(post_id=post.pk))
                    if post.pk:
                        current = SocialPost.objects.select_for_update().filter(pk=post.pk).first()
                        if current is None:
                            raise SocialError("This post was deleted while you were editing. It has not been recreated.")
                        if current.status in {SocialPost.Status.PUBLISHING, SocialPost.Status.POSTED} or current.updated_at != post.updated_at:
                            raise SocialError("This post changed while you were editing. Reload it before saving.")
                    if any(week.auto_scheduled for week in weeks):
                        post.status, post.scheduled_at = SocialPost.Status.DRAFT, None
                    apply_category(post, request.POST)
                    post.save()
                    if photos is not None:
                        save_slides(post, photos)
                    if changing_media:
                        ContentWeek.objects.filter(post_id=post.pk).update(
                            status=ContentWeek.Status.SKIPPED,
                            auto_scheduled=False,
                            review_passed=False,
                            review_note="Photos changed in the composer. Review and schedule this post manually.",
                            updated_at=timezone.now(),
                        )
                if action == "design":
                    return redirect("social:post_design", pk=post.pk)
                if action == "schedule":
                    return redirect(f"{reverse('social:schedule', kwargs={'pk': post.pk})}?return_to={nav['origin']}&via=edit")
                if action == "post_now":
                    claim_and_publish_now(post, actor=request.user, request=request)
                    post.refresh_from_db()
                    if post.status == SocialPost.Status.POSTED:
                        messages.success(request, "The post was published.")
                        return redirect(f"{reverse('social:queue')}?tab=posted")
                    messages.error(request, post.last_error or "The post needs attention.")
                    return redirect(f"{reverse('social:queue')}?tab=attention")
                if auto_generate:
                    request.session[f"social_auto_generate_{post.pk}"] = True
                    notice = "Captions saved. Creating your carousel slides now."
                messages.success(request, notice)
                if action == "save_return":
                    return redirect(nav["url"])
                return redirect(f"{reverse('social:edit', kwargs={'pk': post.pk})}?mode={post.source}&return_to={nav['origin']}")
        except SocialError as exc:
            messages.error(request, str(exc))
            if request.FILES:
                messages.warning(request, "The photos were not saved. Select them again after resolving the error.")
            if post.pk:
                saved = SocialPost.objects.filter(pk=post.pk).first()
                if saved:
                    for field in ("image_data", "image_name", "image_content_type", "carousel_plan"):
                        setattr(post, field, getattr(saved, field))
            if post.pk is None:
                mode = request.POST.get("mode") or mode
    return render(
        request,
        "social/post_form.html",
        {
            "post": post,
            "category_choices": category_choices(post),
            "slides": list(post.slides.all()) if post.pk else [],
            "carousel_pending": generation_pending(post),
            "carousel_prompt_values": request.POST.getlist("carousel_prompt") if request.method == "POST" and "carousel_prompt" in request.POST else post.carousel_plan.get("slides", ["", "", ""]),
            "ideas": ideas,
            "mode": mode,
            "accounts": _accounts(),
            "generate_image": generate_image_checked,
            "generate_carousel": generate_carousel_checked,
            "auto_generate": request.method == "GET" and request.session.pop(f"social_auto_generate_{post.pk}", False) and generation_pending(post),
            "ai_ready": ai_configured(),
            "ai_text_model": text_model(),
            "ai_image_model": image_model(),
        },
    )


@social_editor_required
@require_http_methods(["GET", "POST"])
def schedule(request, pk):
    post = get_object_or_404(SocialPost, pk=pk)
    if post.status not in {SocialPost.Status.DRAFT, SocialPost.Status.SCHEDULED, SocialPost.Status.ATTENTION}:
        messages.error(request, "This post can no longer be rescheduled.")
        return redirect("social:queue")
    current = timezone.localtime(post.scheduled_at, EASTERN) if post.scheduled_at else None
    selected_date = request.GET.get("date") or (current.date().isoformat() if current else "")
    selected_time = request.GET.get("time") or (current.strftime("%H:%M") if current else "09:00")
    today = timezone.now().astimezone(EASTERN).date()
    if not selected_date:
        selected_date = today.isoformat()
    if request.method == "POST":
        try:
            when = parse_eastern(request.POST.get("date", ""), request.POST.get("time", ""))
            schedule_post(post, when, actor=request.user)
        except SocialError as exc:
            messages.error(request, str(exc))
        else:
            local = timezone.localtime(when, EASTERN)
            hour = local.strftime("%I").lstrip("0")
            messages.success(request, f"Scheduled for {local.strftime('%a, %b')} {local.day} · {hour}:{local.strftime('%M %p')} ET.")
            return redirect(f"{reverse('social:queue')}?tab=scheduled")
        selected_date = request.POST.get("date", selected_date)
        selected_time = request.POST.get("time", selected_time)
    try:
        selected = date.fromisoformat(selected_date)
        if not 1901 <= selected.year <= 9998:
            raise ValueError("Date outside calendar range")
    except ValueError:
        selected = today
        selected_date = today.isoformat()
    focus = month_start(request.GET.get("month", selected.strftime("%Y-%m")), today)
    previous = focus - timedelta(days=1)
    following = (focus + timedelta(days=32)).replace(day=1)
    return render(
        request,
        "social/schedule.html",
        {
            "post": post,
            "selected_date": selected_date,
            "selected_time": selected_time,
            "focus": focus,
            "previous": previous.strftime("%Y-%m"),
            "following": following.strftime("%Y-%m"),
            "weeks": _month(focus, today, selected),
            "quick": quick_times(),
            "changing": post.status == SocialPost.Status.SCHEDULED,
        },
    )


@social_editor_required
@require_http_methods(["GET", "POST"])
def cancel(request, pk):
    post = get_object_or_404(SocialPost, pk=pk)
    if post.status != SocialPost.Status.SCHEDULED:
        messages.error(request, "Only a post that is still scheduled can be canceled.")
        return redirect("social:queue")
    if request.method == "POST":
        try:
            cancel_schedule(post, actor=request.user)
        except SocialError as exc:
            messages.error(request, str(exc))
            return redirect("social:queue")
        messages.success(request, "Canceled. The caption is back in Drafts.")
        return redirect(f"{reverse('social:queue')}?tab=drafts")
    return render(request, "social/cancel.html", {"post": post})


@social_editor_required
@require_POST
def retry(request, pk):
    post = get_object_or_404(SocialPost, pk=pk)
    if post.status != SocialPost.Status.ATTENTION:
        messages.error(request, "Only a post that needs attention can be tried again.")
        return redirect("social:queue")
    try:
        with transaction.atomic():
            locked = SocialPost.objects.select_for_update().get(pk=post.pk)
            if locked.status != SocialPost.Status.ATTENTION:
                raise SocialError("This post is already being retried or has been posted.")
            locked.status = SocialPost.Status.PUBLISHING
            locked.save(update_fields=["status", "updated_at"])
        publish_post(locked, request=request)
        locked.refresh_from_db()
    except SocialError as exc:
        messages.error(request, str(exc))
        return redirect(f"{reverse('social:queue')}?tab=attention")
    if locked.status == SocialPost.Status.POSTED:
        messages.success(request, "The post was published.")
        return redirect(f"{reverse('social:queue')}?tab=posted")
    messages.error(request, locked.last_error or "The post still needs attention.")
    return redirect(f"{reverse('social:queue')}?tab=attention")


def image(request, pk):
    """Staff can preview a photo. A short-lived signature lets Facebook or Instagram fetch it."""
    post = get_object_or_404(SocialPost, pk=pk)
    if not post.has_image:
        raise Http404
    signature = request.GET.get("t", "")
    if signature:
        if not signature_matches(pk, signature):
            raise Http404
    elif not can_manage_social(request.user):
        raise Http404
    return HttpResponse(bytes(post.image_data), content_type=post.image_content_type or "image/jpeg")


@social_editor_required
@require_POST
def carousel_generate(request: HttpRequest, pk: int) -> JsonResponse:
    get_object_or_404(SocialPost, pk=pk)
    try:
        completed, total = generate_next_slide(pk)
    except SocialError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"completed": completed, "total": total})


def slide_image(request: HttpRequest, pk: int, slide_id: int) -> HttpResponse:
    from apps.social.meta import slide_signature_matches

    signature = request.GET.get("t", "")
    if signature:
        if not slide_signature_matches(pk, slide_id, signature):
            raise Http404
    elif not can_manage_social(request.user):
        raise Http404
    slide = get_object_or_404(SocialPostSlide, pk=slide_id, post_id=pk)
    response = HttpResponse(bytes(slide.image_data), content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@social_editor_required
def settings_page(request):
    return render(
        request,
        "social/settings.html",
        {"accounts": _accounts(), "ready": meta_ready(), "facebook_connected": connected_account("facebook"), "instagram_connected": connected_account("instagram")},
    )


def _start_sign_in(request, network: str):
    if not meta_ready():
        messages.error(
            request,
            "Meta setup is not complete. Add the app id, secret, both callback addresses, and the encryption keys.",
        )
        return redirect("social:settings")
    if not request.session.session_key:
        request.session.save()
    state = secrets.token_urlsafe(32)
    SocialAuthorization.objects.create(
        state_hash=digest(state),
        user=request.user,
        session_hash=digest(request.session.session_key or ""),
        network=network,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    insights = request.POST.get("insights") == "on"
    url = facebook_authorization_url(state, insights=insights) if network == SocialAccount.Network.FACEBOOK else instagram_authorization_url(state, insights=insights)
    return redirect(url)


def _open_authorization(request, network: str) -> SocialAuthorization:
    if not request.session.session_key:
        raise SocialError("This sign-in expired. Start again from Settings.")
    with transaction.atomic():
        authorization = (
            SocialAuthorization.objects.select_for_update()
            .filter(
                state_hash=digest(request.GET.get("state", "")),
                user=request.user,
                session_hash=digest(request.session.session_key),
                network=network,
                code_used=False,
                consumed=False,
                expires_at__gt=timezone.now(),
            )
            .first()
        )
        if authorization is None:
            raise SocialError("This sign-in expired or was already used. Start again from Settings.")
        authorization.code_used = True
        authorization.save(update_fields=["code_used", "updated_at"])
    if request.GET.get("error") or not request.GET.get("code"):
        raise SocialError("Sign-in was canceled before it finished.")
    return authorization


@social_editor_required
@require_POST
def facebook_connect(request):
    return _start_sign_in(request, SocialAccount.Network.FACEBOOK)


@social_editor_required
def facebook_callback(request):
    try:
        authorization = _open_authorization(request, SocialAccount.Network.FACEBOOK)
        pages = exchange_facebook_code(request.GET.get("code", ""))
    except SocialError as exc:
        messages.error(request, str(exc))
        return redirect("social:settings")
    if not pages:
        messages.error(request, "That Facebook account does not manage a Page. Choose the account that manages ClearCode Reading.")
        return redirect("social:settings")
    if len(pages) == 1:
        save_facebook_page(pages[0], actor=request.user)
        authorization.consumed = True
        authorization.save(update_fields=["consumed", "updated_at"])
        if pages[0].get("instagram_id"):
            messages.success(request, f"Signed in. {pages[0]['name']} and Instagram are ready.")
        else:
            messages.success(request, f"Signed in. {pages[0]['name']} is ready. Instagram can be connected on its own.")
        return redirect("social:settings")
    authorization.encrypted_choices = encrypt_json(pages)
    authorization.awaiting_choice = True
    authorization.save(update_fields=["encrypted_choices", "awaiting_choice", "updated_at"])
    request.session["social_authorization_id"] = authorization.pk
    return redirect("social:choose_page")


@social_editor_required
@require_http_methods(["GET", "POST"])
def choose_page(request):
    authorization = (
        SocialAuthorization.objects.filter(
            pk=request.session.get("social_authorization_id"),
            user=request.user,
            awaiting_choice=True,
            consumed=False,
            expires_at__gt=timezone.now(),
        ).first()
    )
    if authorization is None:
        messages.error(request, "Choose the Page again from Settings. That sign-in expired.")
        return redirect("social:settings")
    try:
        pages = decrypt_json(authorization.encrypted_choices)
    except SocialError as exc:
        messages.error(request, str(exc))
        return redirect("social:settings")
    if request.method == "POST":
        chosen = next((page for page in pages if page.get("id") == request.POST.get("page_id")), None)
        if chosen is None:
            messages.error(request, "Choose one of the Pages from this sign-in.")
        else:
            save_facebook_page(chosen, actor=request.user)
            authorization.consumed = True
            authorization.awaiting_choice = False
            authorization.encrypted_choices = ""
            authorization.save(update_fields=["consumed", "awaiting_choice", "encrypted_choices", "updated_at"])
            request.session.pop("social_authorization_id", None)
            messages.success(request, f"{chosen['name']} is connected.")
            return redirect("social:settings")
    return render(request, "social/choose_page.html", {"pages": [{"id": page["id"], "name": page["name"]} for page in pages]})


@social_editor_required
@require_POST
def instagram_connect(request):
    return _start_sign_in(request, SocialAccount.Network.INSTAGRAM)


@social_editor_required
def instagram_callback(request):
    try:
        authorization = _open_authorization(request, SocialAccount.Network.INSTAGRAM)
        profile = exchange_instagram_code(request.GET.get("code", ""))
        save_instagram_account(profile, actor=request.user)
    except SocialError as exc:
        messages.error(request, str(exc))
        return redirect("social:settings")
    authorization.consumed = True
    authorization.save(update_fields=["consumed", "updated_at"])
    messages.success(request, "Instagram is connected.")
    return redirect("social:settings")


@social_editor_required
@require_POST
def disconnect(request, network):
    if network not in {SocialAccount.Network.FACEBOOK, SocialAccount.Network.INSTAGRAM}:
        raise Http404
    try:
        messages.success(request, disconnect_network(network, actor=request.user))
    except SocialError as exc:
        messages.error(request, str(exc))
    return redirect("social:settings")
