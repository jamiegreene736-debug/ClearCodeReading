"""Draft social captions and an illustration with OpenAI.

Captions use gpt-4o-mini. Images use DALL·E 3. Both calls use SOCIAL_OPENAI_API_KEY
(or OPENAI_API_KEY). A person still has to read the result before it is scheduled.
"""

from __future__ import annotations

import base64
import json
import logging
import re

import requests
from django.conf import settings

from apps.social.exceptions import SocialError

logger = logging.getLogger(__name__)

_SCORE = re.compile(r"\b(scored|score of|grade of|percentile)\b", re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_SYSTEM = """You write public social posts for ClearCode Reading, a reading program for families.
Return only JSON with two string keys: "facebook" and "instagram".
Write new sentences. Do not paste the subject back as the whole caption.
Facebook: two to four warm sentences. Include the link on its own line when one is provided.
Instagram: under 280 characters, then two hashtags. Do not include the link.
Never include a child's name, a school name, a reading score, a grade, or a percentile.
Do not invent a personal story about a real student."""


def ai_configured() -> bool:
    return bool(getattr(settings, "SOCIAL_OPENAI_API_KEY", ""))


def text_model() -> str:
    return getattr(settings, "SOCIAL_AI_TEXT_MODEL", "") or "gpt-4o-mini"


def image_model() -> str:
    return getattr(settings, "SOCIAL_AI_IMAGE_MODEL", "") or "dall-e-3"


def _require_key() -> None:
    if not ai_configured():
        raise SocialError("Add SOCIAL_OPENAI_API_KEY on the server before Draft with AI can run.")


def _guard(text: str) -> None:
    if _SCORE.search(text or ""):
        raise SocialError("Leave out reading scores. Describe the idea without a child's results.")


def _post(url: str, payload: dict, *, timeout: int) -> dict:
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.SOCIAL_OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("social_ai_unreachable error_type=%s", type(exc).__name__)
        raise SocialError("The draft could not be written. Your subject is still here.") from exc
    if response.status_code in {401, 403}:
        logger.warning("social_ai_rejected status=%s", response.status_code)
        raise SocialError("The AI key was rejected. Check SOCIAL_OPENAI_API_KEY.")
    if response.status_code >= 400:
        logger.warning("social_ai_rejected status=%s", response.status_code)
        raise SocialError("The draft could not be written. Your subject is still here.")
    try:
        body = response.json()
    except ValueError as exc:
        raise SocialError("The draft could not be written. Your subject is still here.") from exc
    if not isinstance(body, dict):
        raise SocialError("The draft could not be written. Your subject is still here.")
    return body


def write_captions(*, subject: str, audience: str, tone: str, link: str) -> tuple[str, str]:
    _require_key()
    text = " ".join((subject or "").split())
    if len(text) < 12:
        raise SocialError("Add a subject of at least a short sentence before drafting with AI.")
    _guard(text)
    body = _post(
        "https://api.openai.com/v1/chat/completions",
        {
            "model": text_model(),
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "subject": text,
                            "audience": audience or "families",
                            "tone": tone or "warm",
                            "link": link or "",
                        }
                    ),
                },
            ],
        },
        timeout=40,
    )
    try:
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(_FENCE.sub("", str(content).strip()))
        facebook = " ".join(str(parsed["facebook"]).split())
        instagram = str(parsed["instagram"]).strip()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise SocialError("The draft came back in a shape the portal could not use. Try again.") from exc
    if len(facebook) < 20 or len(instagram) < 10:
        raise SocialError("The draft was too short. Try again.")
    _guard(facebook)
    _guard(instagram)
    return facebook[:5000], instagram[:2200]


def image_prompt(subject: str) -> str:
    return (
        "A simple flat illustration with no text, no letters, no logo, and no people. "
        "Do not depict a child, a face, a school, or a photograph. "
        "Use deep teal, linen, sand, and a little gold. "
        "Show only objects that suggest quiet reading at home, such as a closed book, "
        "a table, and warm light. Square composition. Inspired by this subject, "
        f"without copying any names: {subject[:300]}"
    )


def generate_image(*, subject: str) -> tuple[bytes, str]:
    _require_key()
    text = " ".join((subject or "").split())
    if len(text) < 12:
        raise SocialError("Add a subject before generating an image.")
    _guard(text)
    body = _post(
        "https://api.openai.com/v1/images/generations",
        {
            "model": image_model(),
            "prompt": image_prompt(text),
            "n": 1,
            "size": "1024x1024",
            "response_format": "b64_json",
        },
        timeout=90,
    )
    try:
        encoded = body["data"][0]["b64_json"]
        raw = base64.b64decode(encoded)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise SocialError("The image could not be created. The captions are unchanged.") from exc
    if not raw or len(raw) > 8 * 1024 * 1024:
        raise SocialError("The image could not be created. Try again.")
    return raw, "image/png"
