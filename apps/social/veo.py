"""Google Veo transport. Keys and provider payloads never reach the browser."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

import requests
from django.conf import settings

from apps.social.exceptions import SocialError

logger = logging.getLogger(__name__)
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MAX_VIDEO_BYTES = 64 * 1024 * 1024
DURATION = 8
SETUP_MESSAGE = (
    "AI video generation needs a Google Veo connection. Sora was retired by OpenAI. "
    "Ask an administrator to connect a paid Gemini API key in the server settings. "
    "Your storyboard and captions are saved."
)


class VideoRejected(SocialError):
    """The provider explicitly refused a render request."""


def configured() -> bool:
    return bool(getattr(settings, "SOCIAL_GEMINI_API_KEY", "").strip())


def require_key() -> None:
    if not configured():
        raise SocialError(SETUP_MESSAGE)


def model() -> str:
    value = getattr(settings, "SOCIAL_VEO_MODEL", "veo-3.1-generate-preview")
    if value not in {"veo-3.1-generate-preview", "veo-3.1-fast-generate-preview"}:
        raise SocialError("Choose a supported Veo 3.1 model in the server settings.")
    return str(value)


def _check_response(response: requests.Response) -> None:
    if response.status_code < 300:
        return
    status = response.status_code
    response.close()
    logger.warning("social_veo_rejected status=%s", status)
    message = {
        400: "Veo could not accept the storyboard or video settings. Review the storyboard before trying again.",
        401: "Google rejected the Gemini API key. Ask an administrator to update the Veo connection.",
        403: "Google denied video generation. Check paid Gemini billing, Veo access, and regional eligibility.",
        404: "Google could not find this video model or job. Check the Veo model and project access.",
        429: "Google Veo has reached its quota or rate limit. Check the project's billing and quota before retrying.",
    }.get(status, "Google Veo could not confirm this request. Your saved work is kept.")
    error = VideoRejected if 400 <= status < 500 and status != 408 else SocialError
    raise error(message)


def _request(method: str, path: str, **kwargs: Any) -> requests.Response:
    require_key()
    try:
        response = requests.request(
            method,
            f"{BASE_URL}/{path}",
            headers={"x-goog-api-key": settings.SOCIAL_GEMINI_API_KEY},
            timeout=(10, 60),
            allow_redirects=False,
            **kwargs,
        )
    except requests.RequestException as exc:
        logger.warning("social_veo_unreachable type=%s", type(exc).__name__)
        raise SocialError(
            "Google Veo could not confirm this request. Your saved work is kept."
        ) from exc
    _check_response(response)
    return response


def _json(response: requests.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise SocialError("Google returned an unreadable video response.") from exc
    finally:
        response.close()
    if not isinstance(body, dict):
        raise SocialError("Google returned an unreadable video response.")
    return body


def operation_name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:models/veo-[A-Za-z0-9.-]+/)?operations/[A-Za-z0-9_-]{1,200}", value
    ):
        raise SocialError("Google did not return a usable video job ID.")
    return value


def start_video(prompt: str) -> str:
    result = _json(
        _request(
            "POST",
            f"models/{model()}:predictLongRunning",
            json={
                "instances": [{"prompt": prompt}],
                "parameters": {
                    "aspectRatio": "9:16",
                    "durationSeconds": DURATION,
                    "resolution": "720p",
                    "personGeneration": "allow_all",
                    "sampleCount": 1,
                },
            },
        )
    )
    return operation_name(result.get("name"))


def poll_video(name: str) -> dict[str, Any]:
    result = _json(_request("GET", operation_name(name)))
    if result.get("done") is not True:
        return {"status": "in_progress"}
    response = result.get("response", {})
    generated = (
        response.get("generateVideoResponse", {}) if isinstance(response, dict) else {}
    )
    if result.get("error") or not isinstance(generated, dict):
        return {
            "status": "failed",
            "error": "Google could not complete this video. Review the storyboard and Veo project settings before retrying.",
        }
    samples = generated.get("generatedSamples")
    if not isinstance(samples, list) or not samples:
        return {
            "status": "failed",
            "error": "Google did not return a video. Its content safeguards may have filtered the scene. Revise the storyboard before retrying.",
        }
    first = samples[0]
    video = first.get("video", {}) if isinstance(first, dict) else {}
    uri = video.get("uri") if isinstance(video, dict) else None
    if not isinstance(uri, str):
        raise SocialError(
            "Google returned a video without a download address. Check progress again."
        )
    return {"status": "completed", "uri": uri}


def _download_host(url: str) -> str:
    parts = urlsplit(url)
    try:
        valid_port = parts.port in {None, 443}
    except ValueError:
        valid_port = False
    if parts.scheme != "https" or parts.username or parts.password or not valid_port:
        raise SocialError("Google returned an unsafe video download address.")
    host = parts.hostname or ""
    if (
        host != "generativelanguage.googleapis.com"
        and host != "storage.googleapis.com"
        and not host.endswith(".googleusercontent.com")
    ):
        raise SocialError("Google returned an unexpected video download host.")
    return host


def download_video(url: str) -> bytes:
    require_key()
    for _ in range(4):
        host = _download_host(url)
        headers = (
            {"x-goog-api-key": settings.SOCIAL_GEMINI_API_KEY}
            if host == "generativelanguage.googleapis.com"
            else {}
        )
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=(10, 60),
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise SocialError(
                "The video download stopped. Check progress again to resume."
            ) from exc
        with response:
            if response.status_code in {301, 302, 303, 307, 308}:
                url = response.headers.get("Location", "")
                continue
            _check_response(response)
            chunks = bytearray()
            try:
                for chunk in response.iter_content(256 * 1024):
                    chunks.extend(chunk)
                    if len(chunks) > MAX_VIDEO_BYTES:
                        raise SocialError(
                            "The generated video exceeds the 64 MB limit."
                        )
            except requests.RequestException as exc:
                raise SocialError(
                    "The video download stopped. Check progress again to resume."
                ) from exc
            if len(chunks) < 12 or chunks[4:8] != b"ftyp":
                raise SocialError(
                    "Google returned a file that was not a usable MP4 video."
                )
            return bytes(chunks)
    raise SocialError(
        "The video download redirected too many times. Check progress again."
    )
