"""Persist provider IDs and submission checkpoints before publishing Reels."""

from __future__ import annotations

import re
from typing import Any

from django.db import transaction

from apps.social.crypto import decrypt_text
from apps.social.exceptions import SocialError
from apps.social.meta import _graph, _version
from apps.social.models import SocialAccount, SocialPost
from apps.social.reel_views import video_url


def _save(post: SocialPost, network: str, state: dict[str, Any]) -> None:
    with transaction.atomic():
        locked = SocialPost.objects.select_for_update().get(pk=post.pk)
        locked.reel_publications = {**locked.reel_publications, network: state}
        locked.save(update_fields=["reel_publications", "updated_at"])
        post.reel_publications = locked.reel_publications


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,64}", value):
        raise SocialError("The network did not return a usable video ID.")
    return value


def _result(identifier: str, network: str) -> dict[str, str]:
    return {
        "external_id": identifier,
        "permalink": f"https://www.facebook.com/reel/{identifier}"
        if network == "facebook"
        else "",
    }


def publish_reel(post: SocialPost, account: SocialAccount) -> dict[str, str]:
    if not post.video_data or post.reel_job.get("status") != "completed":
        raise SocialError("Finish generating the Reel before publishing.")
    network = account.network
    state = dict(post.reel_publications.get(network, {}))
    if state.get("published_id"):
        return _result(_identifier(state["published_id"]), network)
    if state.get("account_id") and state["account_id"] != account.external_id:
        raise SocialError(
            "The connected account changed after upload. Reconcile the existing upload before retrying."
        )
    state["account_id"] = account.external_id
    token = decrypt_text(account.encrypted_token)
    if network == SocialAccount.Network.INSTAGRAM:
        return _instagram(post, account, token, state)
    return _facebook(post, account, token, state)


def _instagram(
    post: SocialPost, account: SocialAccount, token: str, state: dict[str, Any]
) -> dict[str, str]:
    network = account.network
    base = (
        f"https://graph.facebook.com/{_version()}"
        if account.via_facebook
        else "https://graph.instagram.com"
    )
    if not state.get("container"):
        if state.get("creating"):
            raise SocialError(
                "Instagram upload was not confirmed. Reconcile the provider request before creating another upload."
            )
        url = video_url(post)
        state["creating"] = True
        _save(post, network, state)
        payload = _graph(
            "POST",
            f"{base}/{account.external_id}/media",
            token=token,
            data={
                "media_type": "REELS",
                "video_url": url,
                "caption": post.instagram_caption.strip(),
                "share_to_feed": "true",
            },
        )
        state["container"] = _identifier(payload.get("id"))
        state["creating"] = False
        _save(post, network, state)
    container = _identifier(state["container"])
    if state.get("submitting"):
        raise SocialError(
            "Instagram has not confirmed publication. Check the account and reconcile this upload before retrying; another Reel will not be created automatically."
        )
    status = _graph(
        "GET", f"{base}/{container}", token=token, params={"fields": "status_code"}
    )
    if status.get("status_code") in {"ERROR", "EXPIRED"}:
        raise SocialError(
            "Instagram could not process this Reel. Check the saved upload with the provider."
        )
    if status.get("status_code") != "FINISHED":
        raise SocialError(
            "Instagram is still processing this Reel. Retry shortly to continue the same upload."
        )
    state["submitting"] = True
    _save(post, network, state)
    payload = _graph(
        "POST",
        f"{base}/{account.external_id}/media_publish",
        token=token,
        data={"creation_id": container},
    )
    identifier = _identifier(payload.get("id"))
    state["published_id"] = identifier
    _save(post, network, state)
    result = _result(identifier, network)
    try:
        details = _graph(
            "GET", f"{base}/{identifier}", token=token, params={"fields": "permalink"}
        )
        result["permalink"] = str(details.get("permalink") or "")
    except SocialError:
        pass  # A failed optional lookup must never repeat a confirmed publication.
    return result


def _facebook(
    post: SocialPost, account: SocialAccount, token: str, state: dict[str, Any]
) -> dict[str, str]:
    network = account.network
    base = f"https://graph.facebook.com/{_version()}"
    endpoint = f"{base}/{account.external_id}/video_reels"
    if not state.get("video_id"):
        if state.get("creating"):
            raise SocialError(
                "Facebook upload was not confirmed. Reconcile the provider request before creating another upload."
            )
        # Validate delivery before creating any remote upload session.
        video_url(post)
        state["creating"] = True
        _save(post, network, state)
        payload = _graph("POST", endpoint, token=token, data={"upload_phase": "start"})
        state["video_id"] = _identifier(payload.get("video_id"))
        state["creating"] = False
        _save(post, network, state)
    identifier = _identifier(state["video_id"])
    if state.get("submitting"):
        status = _graph(
            "GET", f"{base}/{identifier}", token=token, params={"fields": "status"}
        )
        phases = status.get("status", {})
        publishing = (
            phases.get("publishing_phase") if isinstance(phases, dict) else None
        )
        if isinstance(publishing, dict) and publishing.get("status") == "complete":
            state["published_id"] = identifier
            _save(post, network, state)
            return _result(identifier, network)
        raise SocialError(
            "Facebook has not confirmed this Reel yet. Retry shortly to check the same video; no duplicate will be submitted."
        )
    if not state.get("uploaded"):
        # Construct the documented endpoint instead of forwarding tokens to a returned URL.
        payload = _graph(
            "POST",
            f"https://rupload.facebook.com/video-upload/{_version()}/{identifier}",
            headers={"Authorization": f"OAuth {token}", "file_url": video_url(post)},
        )
        if payload.get("success") is not True:
            raise SocialError(
                "Facebook did not confirm the video upload. Retry to resume this upload."
            )
        state["uploaded"] = True
        _save(post, network, state)
    state["submitting"] = True
    _save(post, network, state)
    payload = _graph(
        "POST",
        endpoint,
        token=token,
        data={
            "upload_phase": "finish",
            "video_id": identifier,
            "video_state": "PUBLISHED",
            "description": post.facebook_caption.strip(),
        },
    )
    if payload.get("success") is not True:
        raise SocialError(
            "Facebook has not confirmed the Reel. Retry to check its publication status."
        )
    # A finish acknowledgement begins encoding; only a published phase confirms it is live.
    return _facebook(post, account, token, state)
