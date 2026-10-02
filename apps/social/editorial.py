"""Curated public context. Never query learner, CRM, or inbox records here."""

from __future__ import annotations

from datetime import date

PILLARS = (
    ("reading_routine", "A practical reading routine"),
    ("literacy_explained", "Reading skills, explained simply"),
    ("encouragement", "Confidence and encouragement"),
    ("clearcode_approach", "Get to know ClearCode"),
)
PILLAR_DIRECTIONS = {
    "reading_routine": "Give one small, specific shared-reading routine a family can try today. Show the steps briefly. Avoid vague cozy-nook advice, milestone celebrations, and sweeping outcome claims.",
    "literacy_explained": "Explain one sound-letter or decoding concept in plain language with one accurate written-word example. Do not substitute rhyming or guessing from pictures for decoding. Do not turn this into a general reading-routine post.",
    "encouragement": "Offer one concrete, pressure-free phrase a parent can say to acknowledge effort during reading. Avoid reward systems, comparison, organized achievement celebrations, and promises about confidence or progress.",
    "clearcode_approach": "Introduce ClearCode Reading by name and explain one approved aspect of its explicit, systematic structured-literacy approach. Give a clear parent-friendly explanation, then a gentle invitation to learn more. This must be about ClearCode's approach, not generic family reading tips.",
}

SOURCES = {
    "clearcode": {
        "title": "ClearCode Reading public overview",
        "url": "https://www.clearcodereading.com/about/",
        "facts": "ClearCode Reading focuses on structured literacy and reading support for families and educators. Its approach includes explicit, systematic instruction and attention to progress. Do not imply that a center is open or accepting enrollment, or announce dates, locations, prices, capacity or staff.",
    },
    "reading_basics": {
        "title": "Reading basics: sound–letter connections",
        "url": "https://www.readingrockets.org/literacy-home/reading-101-guide-parents/reading-basics/phonics-and-decoding",
        "facts": "Phonics connects written letters with speech sounds. Decoding involves using those connections to read words. Keep examples general; no diagnoses, statistics or promises about a child's progress.",
    },
    "family_support": {
        "title": "ClearCode family-support editorial guidance",
        "url": "https://www.clearcodereading.com/",
        "facts": "Offer calm, achievable reading routines and encouragement. Celebrate effort without comparing children. Reading together and talking about stories are useful topics. Do not prescribe treatment or claim these activities replace specialist instruction.",
    },
}
CTA_URL = "https://www.clearcodereading.com/"


def pillar_for(week_of: date) -> str:
    return PILLARS[(week_of - date(2026, 1, 5)).days // 7 % len(PILLARS)][0]


def source_context() -> dict[str, dict[str, str]]:
    return {key: dict(value) for key, value in SOURCES.items()}
