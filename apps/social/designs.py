"""Bounded editable image documents; no remote resources or executable markup."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from io import BytesIO
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from apps.social.exceptions import SocialError

MAX_DOCUMENT_BYTES = 12 * 1024 * 1024
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def raster(raw: bytes, *, background: bool = False) -> bytes:
    if not raw or len(raw) > 8 * 1024 * 1024:
        raise SocialError("Use an image smaller than 8 MB.")
    try:
        with Image.open(BytesIO(raw)) as source:
            if (
                source.format not in {"PNG", "JPEG", "WEBP"}
                or source.width * source.height > 16_777_216
            ):
                raise ValueError
            source.verify()
        with Image.open(BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert(
                "RGBA" if background else "RGB"
            )
            image.thumbnail((1440, 1440))
            output = BytesIO()
            image.save(output, "PNG")
            return output.getvalue()
    except (
        OSError,
        ValueError,
        Image.DecompressionBombError,
        UnidentifiedImageError,
    ) as exc:
        raise SocialError(
            "Use a valid PNG, JPEG, or WebP image up to 16 megapixels."
        ) from exc


def number(value: object, low: int, high: int) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise SocialError("A design position or size is outside the canvas limits.")
    return float(value)


def color(value: object) -> str:
    if not isinstance(value, str) or not COLOR.fullmatch(value):
        raise SocialError("Choose a valid design color.")
    return value


def validate_document(raw: str) -> dict[str, Any]:
    if len(raw.encode()) > MAX_DOCUMENT_BYTES:
        raise SocialError("This design is too large. Use smaller background images.")
    try:
        doc = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise SocialError("The design could not be read.") from exc
    if (
        not isinstance(doc, dict)
        or doc.get("version") != 1
        or doc.get("width") != 1080
        or doc.get("height") not in (1080, 1350)
    ):
        raise SocialError("Choose a square or portrait design.")
    pages = doc.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 10:
        raise SocialError("A design needs 1–10 pages.")
    clean_pages = []
    for page in pages:
        if not isinstance(page, dict):
            raise SocialError("A design page could not be read.")
        background = color(page.get("background"))
        image = page.get("image", "")
        if not isinstance(image, str):
            raise SocialError("The background image could not be read.")
        if image:
            try:
                prefix, data = image.split(",", 1)
                if prefix not in {
                    "data:image/jpeg;base64",
                    "data:image/png;base64",
                    "data:image/webp;base64",
                }:
                    raise ValueError
                image = (
                    "data:image/png;base64,"
                    + base64.b64encode(
                        raster(base64.b64decode(data, validate=True), background=True)
                    ).decode()
                )
            except (ValueError, binascii.Error) as exc:
                raise SocialError(
                    "Upload a PNG, JPEG, or WebP background, not a web address."
                ) from exc
        elements = page.get("elements")
        if not isinstance(elements, list) or len(elements) > 32:
            raise SocialError("Use up to 32 text boxes or shapes on each page.")
        clean_elements = []
        for element in elements:
            if not isinstance(element, dict) or element.get("kind") not in (
                "text",
                "box",
            ):
                raise SocialError("This design element is not supported.")
            text = element.get("text", "")
            if not isinstance(text, str) or len(text) > 2000:
                raise SocialError("Use up to 2,000 characters in each text box.")
            if (
                element.get("font") not in ("sans", "serif", "condensed")
                or element.get("align") not in ("left", "center", "right")
                or element.get("weight") not in ("normal", "bold")
                or not isinstance(element.get("strike"), bool)
            ):
                raise SocialError("Choose a supported text style.")
            clean_elements.append(
                {
                    "kind": element["kind"],
                    "text": text,
                    "x": number(element.get("x"), 0, 1080),
                    "y": number(element.get("y"), 0, doc["height"]),
                    "width": number(element.get("width"), 10, 1080),
                    "height": number(element.get("height"), 10, doc["height"]),
                    "size": number(element.get("size"), 12, 220),
                    "font": element["font"],
                    "weight": element["weight"],
                    "align": element["align"],
                    "strike": element["strike"],
                    "color": color(element.get("color")),
                }
            )
        clean_pages.append(
            {"background": background, "image": image, "elements": clean_elements}
        )
    cleaned = {
        "version": 1,
        "width": 1080,
        "height": doc["height"],
        "pages": clean_pages,
    }
    if len(json.dumps(cleaned).encode()) > MAX_DOCUMENT_BYTES:
        raise SocialError("This design is too large. Use smaller background images.")
    return cleaned


def starter(style: str = "scripts") -> dict[str, Any]:
    def text(
        value: str, y: int, size: int, color: str = "#F7F2EA", **extra: object
    ) -> dict[str, object]:
        return {
            "kind": "text",
            "text": value,
            "x": 72,
            "y": y,
            "width": 936,
            "height": min(180, 1080 - y),
            "size": size,
            "font": "sans",
            "weight": "bold",
            "align": "left",
            "strike": False,
            "color": color,
            **extra,
        }

    dark = {
        "background": "#102F38",
        "image": "",
        "elements": [
            text("PARENT SCRIPTS", 65, 24, "#2CB6B6"),
            text("When your child says", 280, 36, weight="normal"),
            text("I’m just\nbad at reading.", 365, 92, height=370, font="condensed"),
            text("Swipe for what to say →", 950, 28, "#BFD6CE", weight="normal"),
        ],
    }
    light = {
        "background": "#F7F2EA",
        "image": "",
        "elements": [
            text("PARENT SCRIPTS", 65, 24, "#102F38"),
            text("INSTEAD OF", 230, 24, "#197B80"),
            text("No you’re not!", 285, 42, "#102F38", strike=True),
            text("", 415, 40, "#CCDCD5", kind="box", height=420),
            text("TRY", 450, 24, "#197B80", x=110, width=850),
            text(
                "Reading feels hard right now. Let’s do one page together.",
                520,
                62,
                "#102F38",
                x=110,
                width=850,
                height=290,
                font="condensed",
            ),
            text("ClearCode Reading", 950, 28, "#197B80"),
        ],
    }
    if style == "tip":
        dark["elements"] = [
            text("READING TIPS", 65, 24, "#2CB6B6"),
            text("One small step.\nA little more confidence.", 280, 88, height=460),
            text("Try ten quiet minutes together today.", 780, 36, weight="normal"),
            text("ClearCode Reading", 950, 28, "#2CB6B6"),
        ]
    if style == "blank":
        return {
            "version": 1,
            "width": 1080,
            "height": 1080,
            "pages": [
                {
                    "background": "#F7F2EA",
                    "image": "",
                    "elements": [
                        text("Your words here", 350, 80, "#102F38", height=350)
                    ],
                }
            ],
        }
    return {
        "version": 1,
        "width": 1080,
        "height": 1080,
        "pages": [dark, light] if style == "scripts" else [dark],
    }
