"""Turn a short brief into a Facebook caption and an Instagram caption.

This stays on the server and does not call an outside writing service. A person
still has to read both captions before anything is scheduled or posted.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from apps.social.exceptions import SocialError

_OPENERS = {
    "warm": "A small change can make reading time calmer.",
    "practical": "Here is a practical idea you can use tonight.",
    "celebratory": "A good moment worth sharing with families.",
}
_TAGS = {
    "families": "#ClearCodeReading #ReadingAtHome",
    "teachers": "#ClearCodeReading #ReadingInstruction",
    "general": "#ClearCodeReading",
}
_SCORE = re.compile(r"\b(scored|score of|grade of|percentile)\b", re.IGNORECASE)


def draft_captions(
    *, brief: str, audience: str, tone: str, link: str
) -> tuple[str, str]:
    text = " ".join((brief or "").split())
    if len(text) < 12:
        raise SocialError("Tell us a little more about the post before drafting.")
    if _SCORE.search(text):
        raise SocialError(
            "Leave out reading scores. Describe the idea without a child's results."
        )
    opener = _OPENERS.get(tone, _OPENERS["warm"])
    facebook = f"{opener} {text}"
    if link:
        facebook = f"{facebook}\n\n{link.strip()}"
    instagram_body = text
    if len(instagram_body) > 280:
        shortened = instagram_body[:277].rsplit(" ", 1)[0]
        instagram_body = shortened + "…"
    tags = _TAGS.get(audience, _TAGS["general"])
    instagram = f"{instagram_body}\n\n{tags}"
    facebook = facebook[:5000]
    return facebook, share_caption_links(facebook, instagram[:2200])


_WEB_URL = re.compile(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+", re.IGNORECASE)


def caption_urls(caption: str) -> list[str]:
    urls: list[str] = []
    validate = URLValidator(schemes=["http", "https"])
    for match in _WEB_URL.finditer(caption):
        url = match.group().rstrip(".,;:!?")
        for opening, closing in [("(", ")"), ("[", "]"), ("{", "}")]:
            while url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
        try:
            validate("https://" + url if url.lower().startswith("www.") else url)
        except ValidationError:
            continue
        if url not in urls:
            urls.append(url)
    return urls


def share_caption_links(facebook: str, instagram: str) -> str:
    """Keep generated URLs identical and visible for review before publishing."""
    existing = set(caption_urls(instagram))
    missing = [url for url in caption_urls(facebook) if url not in existing]
    if not missing:
        return instagram
    result = instagram.rstrip() + "\n\n" + "\n".join(missing)
    if len(result) > 2200:
        raise SocialError(
            "The Instagram caption and shared links exceed 2,200 characters. "
            "Shorten the brief or links and generate the draft again."
        )
    return result
