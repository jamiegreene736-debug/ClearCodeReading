from __future__ import annotations

import calendar
import secrets
from datetime import datetime, timedelta

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.social.access import can_manage_social, social_editor_required
from apps.social.ai import ai_configured, generate_image, image_model, text_model, write_captions
from apps.social.crypto import decrypt_json, digest, encrypt_json
from apps.social.drafts import draft_captions
from apps.social.exceptions import SocialError
from apps.social.meta import (
    exchange_facebook_code,
    exchange_instagram_code,
    facebook_authorization_url,
    instagram_authorization_url,
    meta_ready,
    signature_matches,
)
from apps.social.models import SocialAccount, SocialAuthorization, SocialPost
from apps.social.services import (
    EASTERN,
    cancel_schedule,
    claim_and_publish_now,
    connected_account,
    disconnect_network,
    parse_eastern,
    publish_post,
    quick_times,
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
    post.source = mode
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
    store_image(post, request.FILES.get("image"))
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
    posts = posts.prefetch_related("publications")
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
    mode = request.GET.get("mode") or (post.source if pk else SocialPost.Source.BRIEF)
    generate_image_checked = request.method != "POST" or request.POST.get("generate_image") == "on"
    if request.method == "POST":
        try:
            mode = _apply_post(post, request)
            action = request.POST.get("action") or "save"
            notice = "Draft saved."
            if action == "draft":
                facebook, instagram = draft_captions(
                    brief=post.brief,
                    audience=post.audience,
                    tone=post.tone,
                    link=post.link_url,
                )
                post.facebook_caption = facebook
                post.instagram_caption = instagram
                post.source = SocialPost.Source.BRIEF
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
                    post.source = SocialPost.Source.BRIEF
                    notice = "Drafted with AI. Read both captions before you schedule or post."
                if action == "new_image" or generate_image_checked:
                    try:
                        raw, content_type = generate_image(subject=post.brief)
                    except SocialError as exc:
                        if action == "new_image" or not post.facebook_caption:
                            raise
                        notice = f"{notice} The image was not created: {exc}"
                    else:
                        post.image_data = raw
                        post.image_content_type = content_type
                        post.image_name = "ai-draft.png"
                        if action == "new_image":
                            notice = "A new image is attached. The captions are unchanged."
            if post.created_by_id is None:
                post.created_by = request.user
            if post.status not in {SocialPost.Status.SCHEDULED, SocialPost.Status.ATTENTION}:
                post.status = SocialPost.Status.DRAFT
            post.save()
            if action == "schedule":
                return redirect("social:schedule", pk=post.pk)
            if action == "post_now":
                claim_and_publish_now(post, actor=request.user, request=request)
                post.refresh_from_db()
                if post.status == SocialPost.Status.POSTED:
                    messages.success(request, "The post was published.")
                    return redirect(f"{reverse('social:queue')}?tab=posted")
                messages.error(request, post.last_error or "The post needs attention.")
                return redirect(f"{reverse('social:queue')}?tab=attention")
            messages.success(request, notice)
            return redirect(f"{reverse('social:edit', kwargs={'pk': post.pk})}?mode={post.source}")
        except SocialError as exc:
            messages.error(request, str(exc))
            if post.pk is None:
                mode = request.POST.get("mode") or mode
    return render(
        request,
        "social/post_form.html",
        {
            "post": post,
            "mode": mode,
            "accounts": _accounts(),
            "generate_image": generate_image_checked,
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
    if not selected_date:
        selected_date = timezone.localdate().isoformat()
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
        focus = datetime.strptime(selected_date, "%Y-%m-%d").date() if selected_date else timezone.localdate()
    except ValueError:
        focus = timezone.localdate()
    return render(
        request,
        "social/schedule.html",
        {
            "post": post,
            "selected_date": selected_date,
            "selected_time": selected_time,
            "weeks": _month(focus, current.date() if current else None, focus),
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
    url = facebook_authorization_url(state) if network == SocialAccount.Network.FACEBOOK else instagram_authorization_url(state)
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
