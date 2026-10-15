"""Facebook and Instagram sign-in and publishing.

Provider payloads are not returned to the portal. Callers only see SocialError
text that is safe to show a super administrator.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.urls import reverse
from django.utils import timezone

from apps.social.exceptions import SocialError
from apps.social.models import SocialAccount, SocialPost

logger = logging.getLogger(__name__)

FACEBOOK_SCOPES = (
    "pages_show_list",
    "pages_manage_posts",
    "pages_read_engagement",
    "instagram_basic",
    "instagram_content_publish",
    "business_management",
)
INSTAGRAM_SCOPES = (
    "instagram_business_basic",
    "instagram_business_content_publish",
)
_TIMEOUT = 20


def configuration_errors() -> list[str]:
    missing = []
    if not settings.SOCIAL_META_APP_ID:
        missing.append("SOCIAL_META_APP_ID")
    if not settings.SOCIAL_META_APP_SECRET:
        missing.append("SOCIAL_META_APP_SECRET")
    if not settings.SOCIAL_FACEBOOK_REDIRECT_URI:
        missing.append("SOCIAL_FACEBOOK_REDIRECT_URI")
    if not settings.SOCIAL_INSTAGRAM_REDIRECT_URI:
        missing.append("SOCIAL_INSTAGRAM_REDIRECT_URI")
    for name, uri in (
        ("SOCIAL_FACEBOOK_REDIRECT_URI", settings.SOCIAL_FACEBOOK_REDIRECT_URI),
        ("SOCIAL_INSTAGRAM_REDIRECT_URI", settings.SOCIAL_INSTAGRAM_REDIRECT_URI),
    ):
        if uri and not uri.startswith("https://") and not settings.DEBUG:
            missing.append(f"{name} must use HTTPS")
    if settings.ENABLE_DEMO_ACCESS:
        missing.append("Public demo access must be disabled before connecting social accounts")
    from apps.social.crypto import encryption_ready

    if not encryption_ready():
        missing.append("CRM_EMAIL_ENCRYPTION_KEYS")
    return missing


def meta_ready() -> bool:
    return not configuration_errors()


def _version() -> str:
    version = settings.SOCIAL_META_GRAPH_VERSION or "v21.0"
    return version if version.startswith("v") else f"v{version}"


def facebook_authorization_url(state: str, *, insights: bool = False) -> str:
    return f"https://www.facebook.com/{_version()}/dialog/oauth?" + urlencode(
        {
            "client_id": settings.SOCIAL_META_APP_ID,
            "redirect_uri": settings.SOCIAL_FACEBOOK_REDIRECT_URI,
            "state": state,
            "response_type": "code",
            "scope": ",".join(FACEBOOK_SCOPES + (("read_insights", "instagram_manage_insights") if insights else ())),
        }
    )


def instagram_authorization_url(state: str, *, insights: bool = False) -> str:
    return "https://www.instagram.com/oauth/authorize?" + urlencode(
        {
            "client_id": settings.SOCIAL_META_APP_ID,
            "redirect_uri": settings.SOCIAL_INSTAGRAM_REDIRECT_URI,
            "state": state,
            "response_type": "code",
            "scope": ",".join(INSTAGRAM_SCOPES + (("instagram_business_manage_insights",) if insights else ())),
        }
    )


def _graph(method: str, url: str, *, token: str = "", **kwargs: Any) -> dict[str, Any]:
    headers = kwargs.pop("headers", {})
    if token:
        headers = {**headers, "Authorization": f"Bearer {token}"}
    try:
        response = requests.request(method, url, headers=headers, timeout=_TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        logger.warning("social_provider_unreachable error_type=%s", type(exc).__name__)
        raise SocialError("The social network could not be reached. Try again in a moment.") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400 or not isinstance(payload, dict):
        code = payload.get("error", {}).get("code") if isinstance(payload, dict) else None
        logger.warning("social_provider_rejected status=%s code=%s", response.status_code, code)
        if response.status_code in {401, 403} or code in {10, 102, 190}:
            raise SocialError("Sign in again. The saved connection is no longer accepted.")
        raise SocialError("The social network rejected this request.")
    return payload


def exchange_facebook_code(code: str) -> list[dict[str, str]]:
    """Return the Pages this sign-in can publish as. Tokens stay in the result for encrypted storage."""
    short = _graph(
        "GET",
        f"https://graph.facebook.com/{_version()}/oauth/access_token",
        params={
            "client_id": settings.SOCIAL_META_APP_ID,
            "client_secret": settings.SOCIAL_META_APP_SECRET,
            "redirect_uri": settings.SOCIAL_FACEBOOK_REDIRECT_URI,
            "code": code,
        },
    )
    long_lived = _graph(
        "GET",
        f"https://graph.facebook.com/{_version()}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.SOCIAL_META_APP_ID,
            "client_secret": settings.SOCIAL_META_APP_SECRET,
            "fb_exchange_token": short.get("access_token", ""),
        },
    )
    user_token = str(long_lived.get("access_token") or "")
    if not user_token:
        raise SocialError("Facebook did not finish sign-in. Try again.")
    accounts = _graph(
        "GET",
        f"https://graph.facebook.com/{_version()}/me/accounts",
        token=user_token,
        params={"fields": "id,name,access_token,instagram_business_account{id,username}"},
    )
    pages = []
    for item in accounts.get("data") or []:
        if not item.get("id") or not item.get("access_token"):
            continue
        instagram = item.get("instagram_business_account") or {}
        pages.append(
            {
                "id": str(item["id"]),
                "name": str(item.get("name") or "Facebook Page"),
                "token": str(item["access_token"]),
                "instagram_id": str(instagram.get("id") or ""),
                "instagram_username": str(instagram.get("username") or ""),
            }
        )
    return pages


def exchange_instagram_code(code: str) -> dict[str, str]:
    try:
        response = requests.post(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id": settings.SOCIAL_META_APP_ID,
                "client_secret": settings.SOCIAL_META_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": settings.SOCIAL_INSTAGRAM_REDIRECT_URI,
                "code": code,
            },
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("social_instagram_unreachable error_type=%s", type(exc).__name__)
        raise SocialError("Instagram could not be reached. Try again in a moment.") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400 or not isinstance(payload, dict) or not payload.get("access_token"):
        logger.warning("social_instagram_rejected status=%s", response.status_code)
        raise SocialError("Instagram did not finish sign-in. Use a professional account and try again.")
    short_token = str(payload["access_token"])
    long_lived = _graph(
        "GET",
        "https://graph.instagram.com/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": settings.SOCIAL_META_APP_SECRET,
            "access_token": short_token,
        },
    )
    token = str(long_lived.get("access_token") or short_token)
    profile = _graph(
        "GET",
        "https://graph.instagram.com/me",
        token=token,
        params={"fields": "user_id,username"},
    )
    user_id = str(profile.get("user_id") or profile.get("id") or "")
    username = str(profile.get("username") or "")
    if not user_id:
        raise SocialError("Instagram did not share an account id. Try the sign-in again.")
    expires_in = int(long_lived.get("expires_in") or 0)
    return {
        "id": user_id,
        "username": username,
        "token": token,
        "expires_in": str(expires_in),
    }


def image_signature(post_id: int) -> str:
    return TimestampSigner(salt="social-post-image").sign(str(post_id))


def public_base_url() -> str:
    configured = getattr(settings, "SOCIAL_PUBLIC_BASE_URL", "") or ""
    if configured:
        return configured.rstrip("/")
    from urllib.parse import urlsplit

    parts = urlsplit(settings.SOCIAL_FACEBOOK_REDIRECT_URI or "")
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return ""


def absolute_image_url(post: SocialPost) -> str:
    base = public_base_url()
    if not base:
        raise SocialError("Set SOCIAL_PUBLIC_BASE_URL so Facebook and Instagram can fetch the photo.")
    signature = image_signature(post.pk)
    path = reverse("social:image", kwargs={"pk": post.pk})
    return f"{base}{path}?t={signature}"


def signature_matches(post_id: int, signature: str, *, max_age: int = 3600) -> bool:
    try:
        value = TimestampSigner(salt="social-post-image").unsign(signature, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return False
    return value == str(post_id)


def slide_signature_matches(post_id: int, slide_id: int, signature: str) -> bool:
    try:
        value = TimestampSigner(salt="social-carousel-slide").unsign(
            signature, max_age=3600
        )
    except (BadSignature, SignatureExpired):
        return False
    return value == f"{post_id}:{slide_id}"


def carousel_image_urls(post: SocialPost, cover_url: str) -> list[str]:
    urls = [cover_url] if cover_url else []
    for slide in post.slides.all():
        signature = TimestampSigner(salt="social-carousel-slide").sign(
            f"{post.pk}:{slide.pk}"
        )
        path = reverse(
            "social:slide_image", kwargs={"pk": post.pk, "slide_id": slide.pk}
        )
        urls.append(f"{public_base_url()}{path}?{urlencode({'t': signature})}")
    if len(urls) > 10:
        raise SocialError("A post can have up to 10 photos.")
    return urls


def publish_facebook(
    post: SocialPost,
    account: SocialAccount,
    *,
    image_url: str,
    image_urls: list[str] | None = None,
) -> dict[str, str]:
    from apps.social.crypto import decrypt_text

    token = decrypt_text(account.encrypted_token)
    version = _version()
    caption = post.facebook_caption.strip()
    if image_urls and len(image_urls) > 1 and not post.is_blog_promotion:
        if len(image_urls) > 10:
            raise SocialError("A post can have up to 10 photos.")
        attachments = []
        for index, url in enumerate(image_urls, 1):
            uploaded = _graph(
                "POST",
                f"https://graph.facebook.com/{version}/{account.external_id}/photos",
                token=token,
                data={"url": url, "published": "false"},
            )
            photo_id = str(uploaded.get("id") or "")
            if not photo_id:
                raise SocialError(
                    f"Facebook did not accept photo {index}. No carousel was published."
                )
            attachments.append({"media_fbid": photo_id})
        payload = _graph(
            "POST",
            f"https://graph.facebook.com/{version}/{account.external_id}/feed",
            token=token,
            data={"message": caption, "attached_media": json.dumps(attachments)},
        )
    elif post.has_image and image_url and not post.is_blog_promotion:
        payload = _graph(
            "POST",
            f"https://graph.facebook.com/{version}/{account.external_id}/photos",
            token=token,
            data={"url": image_url, "caption": caption},
        )
    else:
        data = {"message": caption}
        if post.link_url:
            data["link"] = post.link_url
        payload = _graph(
            "POST",
            f"https://graph.facebook.com/{version}/{account.external_id}/feed",
            token=token,
            data=data,
        )
    external_id = str(payload.get("post_id") or payload.get("id") or "")
    if not external_id:
        raise SocialError("Facebook did not confirm the post.")
    permalink = ""
    try:
        details = _graph(
            "GET",
            f"https://graph.facebook.com/{version}/{external_id}",
            token=token,
            params={"fields": "permalink_url"},
        )
        permalink = str(details.get("permalink_url") or "")
    except SocialError:
        permalink = f"https://www.facebook.com/{external_id}"
    return {"external_id": external_id, "permalink": permalink}


def publish_instagram(
    post: SocialPost,
    account: SocialAccount,
    *,
    image_url: str,
    image_urls: list[str] | None = None,
) -> dict[str, str]:
    from apps.social.crypto import decrypt_text

    if not image_url:
        raise SocialError("Instagram needs a photo.")
    token = decrypt_text(account.encrypted_token)
    version = _version()
    base = (
        f"https://graph.facebook.com/{version}"
        if account.via_facebook
        else "https://graph.instagram.com"
    )
    urls = image_urls or [image_url]
    if not 1 <= len(urls) <= 10:
        raise SocialError("Choose up to 10 photos.")
    children = []
    for index, url in enumerate(urls, 1):
        data = {"image_url": url}
        if len(urls) > 1:
            data["is_carousel_item"] = "true"
        else:
            data["caption"] = post.instagram_caption.strip()
        container = _graph(
            "POST", f"{base}/{account.external_id}/media", token=token, data=data
        )
        creation_id = str(container.get("id") or "")
        if not creation_id:
            raise SocialError(f"Instagram did not accept photo {index}.")
        _wait_for_container(base, token, creation_id, label=f"photo {index}")
        children.append(creation_id)
    creation_id = children[0]
    if len(children) > 1:
        parent = _graph(
            "POST",
            f"{base}/{account.external_id}/media",
            token=token,
            data={
                "media_type": "CAROUSEL",
                "children": ",".join(children),
                "caption": post.instagram_caption.strip(),
            },
        )
        creation_id = str(parent.get("id") or "")
        if not creation_id:
            raise SocialError("Instagram did not accept the carousel.")
        _wait_for_container(base, token, creation_id, label="carousel")
    published = _graph(
        "POST",
        f"{base}/{account.external_id}/media_publish",
        token=token,
        data={"creation_id": creation_id},
    )
    external_id = str(published.get("id") or "")
    if not external_id:
        raise SocialError("Instagram did not confirm the post.")
    # A permalink lookup must not turn a confirmed publication into a retry/duplicate.
    try:
        details = _graph(
            "GET", f"{base}/{external_id}", token=token, params={"fields": "permalink"}
        )
        permalink = str(details.get("permalink") or "")
    except SocialError:
        permalink = ""
    return {"external_id": external_id, "permalink": permalink}


def _wait_for_container(base: str, token: str, creation_id: str, *, label: str) -> None:
    for attempt in range(5):
        status = _graph(
            "GET",
            f"{base}/{creation_id}",
            token=token,
            params={"fields": "status_code"},
        )
        code = status.get("status_code")
        if code == "FINISHED":
            return
        if code in {"ERROR", "EXPIRED"}:
            raise SocialError(
                f"Instagram could not prepare {label}. No carousel was published."
            )
        if attempt < 4:
            time.sleep(1)
    raise SocialError(f"Instagram is still preparing {label}. Try again in a moment.")


def token_expiry(expires_in: int):
    if expires_in <= 0:
        return None
    return timezone.now() + timedelta(seconds=expires_in)
