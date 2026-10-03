"""Scheduling, cancellation, and the publish step for social posts."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.social.crypto import decrypt_text, encrypt_text
from apps.social.exceptions import SocialError
from apps.social.models import ContentPlan, ContentWeek, SocialAccount, SocialPost, SocialPublication
from apps.users.models import AuditLog, CustomUser

logger = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def connected_account(network: str) -> SocialAccount | None:
    account = SocialAccount.objects.filter(network=network).first()
    if account and account.is_connected:
        return account
    return None


def parse_eastern(date_text: str, time_text: str) -> datetime:
    try:
        day = datetime.strptime(date_text, "%Y-%m-%d").date()
        clock = datetime.strptime(time_text, "%H:%M").time()
    except ValueError as exc:
        raise SocialError("Choose a valid date and time.") from exc
    try:
        local = datetime.combine(day, clock, tzinfo=EASTERN)
    except ValueError as exc:
        raise SocialError("That time does not exist in Eastern Time. Choose another.") from exc
    if local <= timezone.now():
        raise SocialError("Choose a time in the future. Times are Eastern.")
    return local


def quick_times(now: datetime | None = None) -> list[tuple[str, datetime]]:
    local = (now or timezone.now()).astimezone(EASTERN)
    tomorrow = (local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    days_until_thursday = (3 - local.weekday()) % 7
    thursday = (local + timedelta(days=days_until_thursday)).replace(hour=12, minute=0, second=0, microsecond=0)
    if thursday <= local:
        thursday = thursday + timedelta(days=7)
    days_until_monday = (0 - local.weekday()) % 7 or 7
    monday = (local + timedelta(days=days_until_monday)).replace(hour=8, minute=0, second=0, microsecond=0)
    return [
        ("Tomorrow 9:00 AM", tomorrow),
        ("Thursday 12:00 PM", thursday),
        ("Monday 8:00 AM", monday),
    ]


def store_image(post: SocialPost, upload) -> None:
    if upload is None:
        return
    content_type = (getattr(upload, "content_type", "") or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise SocialError("Use a JPEG, PNG, or WebP photo.")
    data = upload.read()
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise SocialError("The photo must be under 8 MB.")
    post.image_data = data
    post.image_content_type = content_type
    post.image_name = (getattr(upload, "name", "") or "photo")[:200]


def _require_publishable(post: SocialPost) -> None:
    if post.is_blog_promotion:
        from apps.blog.promotion import validate_promotion
        validate_promotion(post)
    networks = post.selected_networks()
    if not networks:
        raise SocialError("Choose Facebook, Instagram, or both.")
    if post.post_to_facebook and not post.facebook_caption.strip():
        raise SocialError("Write the Facebook caption before scheduling.")
    if post.post_to_instagram and not post.instagram_caption.strip():
        raise SocialError("Write the Instagram caption before scheduling.")
    if post.post_to_instagram and not post.has_image:
        raise SocialError("Instagram needs a photo.")
    for network in networks:
        if connected_account(network) is None:
            label = "Facebook" if network == SocialAccount.Network.FACEBOOK else "Instagram"
            raise SocialError(f"Connect {label} in Settings before this can be scheduled.")


def schedule_post(post: SocialPost, when: datetime, *, actor: CustomUser) -> None:
    with transaction.atomic():
        ContentPlan.objects.select_for_update().filter(pk=1).first()
        locked = SocialPost.objects.select_for_update().filter(pk=post.pk).first()
        if locked is None:
            raise SocialError("This post has been deleted.")
        if locked.status not in {
            SocialPost.Status.DRAFT,
            SocialPost.Status.SCHEDULED,
            SocialPost.Status.ATTENTION,
        }:
            raise SocialError("This post can no longer be rescheduled.")
        _require_publishable(locked)
        if locked.is_blog_promotion:
            from apps.blog.promotion import validate_promotion
            validate_promotion(locked, when=when)
        ContentWeek.objects.filter(post=locked).update(
            auto_scheduled=False, status=ContentWeek.Status.SKIPPED
        )
        locked.blog_auto_scheduled = False
        locked.scheduled_at = when
        locked.status = SocialPost.Status.SCHEDULED
        locked.last_error = ""
        locked.save(
            update_fields=["scheduled_at", "status", "last_error", "link_url", "blog_publication_at", "blog_auto_scheduled", "updated_at"]
        )
    AuditLog.objects.create(
        actor=actor,
        action="marketing.social.scheduled",
        entity_type="SocialPost",
        entity_id=str(post.pk),
        after={"scheduled_at": when.isoformat()},
    )


def cancel_schedule(post: SocialPost, *, actor: CustomUser) -> None:
    with transaction.atomic():
        ContentPlan.objects.select_for_update().filter(pk=1).first()
        locked = SocialPost.objects.select_for_update().filter(pk=post.pk).first()
        if locked is None:
            raise SocialError("This post has been deleted.")
        if locked.status != SocialPost.Status.SCHEDULED:
            raise SocialError("Only a post that is still scheduled can be canceled.")
        ContentWeek.objects.filter(post=locked).update(
            auto_scheduled=False, status=ContentWeek.Status.SKIPPED
        )
        was = locked.scheduled_at.isoformat() if locked.scheduled_at else ""
        locked.status = SocialPost.Status.DRAFT
        locked.scheduled_at = None
        locked.last_error = ""
        locked.save(
            update_fields=["status", "scheduled_at", "last_error", "updated_at"]
        )
    AuditLog.objects.create(
        actor=actor,
        action="marketing.social.canceled",
        entity_type="SocialPost",
        entity_id=str(post.pk),
        before={"scheduled_at": was},
    )


def _clear_account(account: SocialAccount) -> None:
    account.status = SocialAccount.Status.DISCONNECTED
    account.external_id = ""
    account.display_name = ""
    account.username = ""
    account.encrypted_token = ""
    account.token_expires_at = None
    account.via_facebook = False
    account.connected_at = None
    account.last_error = ""
    account.save()


def disconnect_network(network: str, *, actor: CustomUser) -> str:
    account = SocialAccount.objects.filter(network=network).first()
    if account is None or account.status == SocialAccount.Status.DISCONNECTED:
        return "That network is already disconnected."
    also_instagram = False
    with transaction.atomic():
        _clear_account(account)
        if network == SocialAccount.Network.FACEBOOK:
            linked = SocialAccount.objects.filter(
                network=SocialAccount.Network.INSTAGRAM,
                via_facebook=True,
            ).exclude(status=SocialAccount.Status.DISCONNECTED)
            for item in linked:
                _clear_account(item)
                also_instagram = True
        label = "Facebook" if network == SocialAccount.Network.FACEBOOK else "Instagram"
        waiting = SocialPost.objects.select_for_update().filter(status=SocialPost.Status.SCHEDULED)
        if network == SocialAccount.Network.FACEBOOK:
            waiting = waiting.filter(post_to_facebook=True)
        else:
            waiting = waiting.filter(post_to_instagram=True)
        waiting.update(
            status=SocialPost.Status.ATTENTION,
            last_error=f"{label} was disconnected before this could post.",
        )
    AuditLog.objects.create(
        actor=actor,
        action="marketing.social.disconnected",
        entity_type="SocialAccount",
        entity_id=network,
    )
    if also_instagram:
        return "Facebook was disconnected, and so was the Instagram account from that same sign-in."
    return f"{label} was disconnected."


def save_facebook_page(page: dict[str, str], *, actor: CustomUser) -> None:
    now = timezone.now()
    account, _ = SocialAccount.objects.get_or_create(network=SocialAccount.Network.FACEBOOK)
    account.status = SocialAccount.Status.CONNECTED
    account.external_id = page["id"]
    account.display_name = page["name"][:200]
    account.username = ""
    account.encrypted_token = encrypt_text(page["token"])
    account.via_facebook = False
    account.connected_by = actor
    account.connected_at = now
    account.last_error = ""
    account.last_checked_at = now
    account.save()
    if page.get("instagram_id"):
        instagram, _ = SocialAccount.objects.get_or_create(network=SocialAccount.Network.INSTAGRAM)
        instagram.status = SocialAccount.Status.CONNECTED
        instagram.external_id = page["instagram_id"]
        username = page.get("instagram_username") or ""
        instagram.username = username[:200]
        instagram.display_name = f"@{username}" if username else "Instagram"
        instagram.encrypted_token = encrypt_text(page["token"])
        instagram.via_facebook = True
        instagram.connected_by = actor
        instagram.connected_at = now
        instagram.last_error = ""
        instagram.last_checked_at = now
        instagram.save()
    AuditLog.objects.create(
        actor=actor,
        action="marketing.social.connected",
        entity_type="SocialAccount",
        entity_id=SocialAccount.Network.FACEBOOK,
        after={"page": page["name"], "instagram": bool(page.get("instagram_id"))},
    )


def save_instagram_account(profile: dict[str, str], *, actor: CustomUser) -> None:
    now = timezone.now()
    account, _ = SocialAccount.objects.get_or_create(network=SocialAccount.Network.INSTAGRAM)
    account.status = SocialAccount.Status.CONNECTED
    account.external_id = profile["id"]
    username = profile.get("username") or ""
    account.username = username[:200]
    account.display_name = f"@{username}" if username else "Instagram"
    account.encrypted_token = encrypt_text(profile["token"])
    account.via_facebook = False
    account.connected_by = actor
    account.connected_at = now
    account.last_error = ""
    account.last_checked_at = now
    expires_in = int(profile.get("expires_in") or 0)
    account.token_expires_at = timezone.now() + timedelta(seconds=expires_in) if expires_in else None
    account.save()
    AuditLog.objects.create(
        actor=actor,
        action="marketing.social.connected",
        entity_type="SocialAccount",
        entity_id=SocialAccount.Network.INSTAGRAM,
        after={"username": username},
    )


def _record(post: SocialPost, network: str, *, status: str, external_id: str = "", permalink: str = "", error: str = "") -> None:
    SocialPublication.objects.update_or_create(
        post=post,
        network=network,
        defaults={
            "status": status,
            "external_id": external_id,
            "permalink": permalink,
            "published_at": timezone.now() if status == SocialPublication.Status.PUBLISHED else None,
            "error": error,
        },
    )


def publish_post(post: SocialPost, *, request=None) -> SocialPost:
    """Publish every selected network that does not already have a live post."""
    from apps.social.meta import absolute_image_url, publish_facebook, publish_instagram

    if post.is_blog_promotion:
        from apps.blog.promotion import validate_promotion
        try:
            validate_promotion(post, sending=True)
        except SocialError as exc:
            post.status, post.last_error = SocialPost.Status.ATTENTION, str(exc)[:300]
            post.save(update_fields=["status", "last_error", "updated_at"])
            raise
        post.save(update_fields=["link_url", "blog_publication_at", "updated_at"])
    image_url = ""
    if post.has_image and not post.is_blog_promotion:
        try:
            image_url = absolute_image_url(post)
        except SocialError as exc:
            image_url = ""
            if post.post_to_instagram or post.post_to_facebook:
                post.last_error = str(exc)
    failures = []
    missing_image_url = bool(post.has_image and not image_url and not post.is_blog_promotion)
    for network in post.selected_networks():
        existing = post.publications.filter(network=network, status=SocialPublication.Status.PUBLISHED).first()
        if existing:
            continue
        if missing_image_url:
            message = post.last_error or "Set SOCIAL_PUBLIC_BASE_URL so the photo can be published."
            _record(post, network, status=SocialPublication.Status.FAILED, error=message[:300])
            failures.append(message)
            continue
        account = connected_account(network)
        if account is None:
            label = "Facebook" if network == SocialAccount.Network.FACEBOOK else "Instagram"
            message = f"Connect {label} in Settings, then try again."
            _record(post, network, status=SocialPublication.Status.FAILED, error=message)
            failures.append(message)
            continue
        try:
            if network == SocialAccount.Network.FACEBOOK:
                result = publish_facebook(post, account, image_url=image_url)
            else:
                result = publish_instagram(post, account, image_url=image_url)
        except SocialError as exc:
            if str(exc).startswith("Sign in again"):
                account.status = SocialAccount.Status.RECONNECT
                account.last_error = "Sign in again to keep publishing."
                account.save(update_fields=["status", "last_error", "updated_at"])
            _record(post, network, status=SocialPublication.Status.FAILED, error=str(exc)[:300])
            failures.append(str(exc))
            continue
        _record(
            post,
            network,
            status=SocialPublication.Status.PUBLISHED,
            external_id=result["external_id"],
            permalink=result["permalink"],
        )
        account.last_checked_at = timezone.now()
        account.last_error = ""
        account.save(update_fields=["last_checked_at", "last_error", "updated_at"])
    post.refresh_from_db()
    published = set(
        post.publications.filter(status=SocialPublication.Status.PUBLISHED).values_list("network", flat=True)
    )
    if set(post.selected_networks()).issubset(published) and post.selected_networks():
        post.status = SocialPost.Status.POSTED
        post.last_error = ""
    else:
        post.status = SocialPost.Status.ATTENTION
        post.last_error = failures[0][:300] if failures else "This post needs attention."
    post.save(update_fields=["status", "last_error", "updated_at"])
    return post


def publish_due(*, request=None) -> int:
    """Claim due scheduled posts, then publish them. Returns how many were claimed."""
    now = timezone.now()
    due_ids = list(
        SocialPost.objects.filter(
            status=SocialPost.Status.SCHEDULED, scheduled_at__lte=now
        ).values_list("pk", flat=True)
    )
    claimed = []
    for pk in due_ids:
        with transaction.atomic():
            plan = ContentPlan.objects.select_for_update().filter(pk=1).first()
            from apps.blog.models import BlogContentPlan
            blog_plan = BlogContentPlan.objects.select_for_update().filter(pk=1).first()
            post = SocialPost.objects.select_for_update().filter(pk=pk).first()
            if post is None:
                continue
            blog_automatic = post.blog_auto_scheduled
            if blog_automatic:
                from apps.social.access import can_manage_social
                if blog_plan is None or blog_plan.mode != BlogContentPlan.Mode.AUTOMATIC or blog_plan.updated_by is None or not can_manage_social(blog_plan.updated_by):
                    if post.status == SocialPost.Status.SCHEDULED:
                        post.status, post.scheduled_at = SocialPost.Status.DRAFT, None
                        post.save(update_fields=["status", "scheduled_at", "updated_at"])
                    continue
            automatic = ContentWeek.objects.filter(
                post=post, auto_scheduled=True
            ).exists()
            if automatic:
                from apps.social.access import can_manage_social

                if (
                    plan is None
                    or plan.mode != ContentPlan.Mode.AUTOMATIC
                    or plan.updated_by is None
                    or not can_manage_social(plan.updated_by)
                ):
                    if post.status == SocialPost.Status.SCHEDULED:
                        post.status, post.scheduled_at = SocialPost.Status.DRAFT, None
                        post.save(
                            update_fields=["status", "scheduled_at", "updated_at"]
                        )
                    continue
            if (
                post.status != SocialPost.Status.SCHEDULED
                or post.scheduled_at is None
                or post.scheduled_at > now
            ):
                continue
            post.status = SocialPost.Status.PUBLISHING
            post.save(update_fields=["status", "updated_at"])
            claimed.append(pk)
    for pk in claimed:
        post = SocialPost.objects.get(pk=pk)
        try:
            publish_post(post, request=request)
        except SocialError as exc:
            SocialPost.objects.filter(pk=pk, status=SocialPost.Status.PUBLISHING).update(status=SocialPost.Status.ATTENTION, last_error=str(exc)[:300])
        except Exception:
            logger.exception("social_publish_failed post=%s", pk)
            SocialPost.objects.filter(
                pk=pk, status=SocialPost.Status.PUBLISHING
            ).update(
                status=SocialPost.Status.ATTENTION,
                last_error="Publishing stopped before the network confirmed it. Try again from Needs attention.",
            )
    return len(claimed)


def claim_and_publish_now(post: SocialPost, *, actor: CustomUser, request) -> SocialPost:
    with transaction.atomic():
        locked = SocialPost.objects.select_for_update().filter(pk=post.pk).first()
        if locked is None:
            raise SocialError("This post has been deleted.")
        if locked.status == SocialPost.Status.PUBLISHING:
            raise SocialError("This post is already being sent.")
        _require_publishable(locked)
        locked.status = SocialPost.Status.PUBLISHING
        locked.scheduled_at = timezone.now()
        locked.last_error = ""
        locked.save(update_fields=["status", "scheduled_at", "last_error", "updated_at"])
    AuditLog.objects.create(
        actor=actor,
        action="marketing.social.posted_now",
        entity_type="SocialPost",
        entity_id=str(post.pk),
    )
    return publish_post(locked, request=request)


def token_for_tests(account: SocialAccount) -> str:
    """Used by tests to confirm a token round-trips. Not for views."""
    return decrypt_text(account.encrypted_token)
