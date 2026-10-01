"""Turn a short brief into a Facebook caption and an Instagram caption.

This stays on the server and does not call an outside writing service. A person
still has to read both captions before anything is scheduled or posted.
"""

from __future__ import annotations

import re

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


def draft_captions(*, brief: str, audience: str, tone: str, link: str) -> tuple[str, str]:
    text = " ".join((brief or "").split())
    if len(text) < 12:
        raise SocialError("Tell us a little more about the post before drafting.")
    if _SCORE.search(text):
        raise SocialError("Leave out reading scores. Describe the idea without a child's results.")
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
    return facebook[:5000], instagram[:2200]
