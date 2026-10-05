"""Cloudflare Turnstile checks for public form posts.

Production requires both environment variables:

* ``TURNSTILE_SITE_KEY`` — widget site key, rendered in the browser
* ``TURNSTILE_SECRET_KEY`` — server secret used for siteverify

A post with a missing, blank, or rejected token is not saved. Local debug
without those variables uses Cloudflare's published dummy keys so a real
browser can still submit. The test runner accepts only ``CAPTCHA_MOCK_TOKEN``
and does not call Cloudflare.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

CAPTCHA_FIELD = "cf-turnstile-response"
CAPTCHA_MOCK_TOKEN = "test-captcha-pass"
CAPTCHA_MESSAGE = "Please complete the security check and submit the form again."
SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Cloudflare's published dummy keys. The secret always returns success, so it
# is only used when DEBUG is on and no real secret is configured.
DUMMY_SITE_KEY = "1x00000000000000000000AA"
DUMMY_SECRET = "1x0000000000000000000000000000000AA"

_mock_enabled = False
_missing_secret_logged = False


def enable_captcha_mock() -> None:
    global _mock_enabled
    _mock_enabled = True


def disable_captcha_mock() -> None:
    global _mock_enabled
    _mock_enabled = False


def mock_captcha_enabled() -> bool:
    return _mock_enabled


def turnstile_site_key() -> str:
    configured = str(getattr(settings, "TURNSTILE_SITE_KEY", "") or "").strip()
    if configured:
        return configured
    if settings.DEBUG or _mock_enabled:
        return DUMMY_SITE_KEY
    return ""


def turnstile_secret() -> str:
    global _missing_secret_logged
    configured = str(getattr(settings, "TURNSTILE_SECRET_KEY", "") or "").strip()
    if configured:
        return configured
    if settings.DEBUG:
        return DUMMY_SECRET
    if not _missing_secret_logged:
        _missing_secret_logged = True
        logger.error(
            "TURNSTILE_SECRET_KEY is not set. Public form submissions will be rejected."
        )
    return ""


def posted_captcha_token(request) -> str:
    return str(request.POST.get(CAPTCHA_FIELD, "") or "").strip()


def captcha_ok(request) -> bool:
    """True only when this post carries a token that verifies."""
    token = posted_captcha_token(request)
    if not token:
        return False
    if _mock_enabled:
        return token == CAPTCHA_MOCK_TOKEN
    return verify_turnstile_token(token)


def verify_turnstile_token(token: str) -> bool:
    """Ask Cloudflare siteverify. Fail closed on any error or ``success: false``."""
    secret = turnstile_secret()
    cleaned = str(token or "").strip()
    if not secret or not cleaned:
        return False
    payload = urllib.parse.urlencode({"secret": secret, "response": cleaned}).encode()
    request = urllib.request.Request(SITEVERIFY_URL, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        UnicodeError,
        ValueError,
    ) as exc:
        logger.warning("Turnstile siteverify did not complete: %s", exc)
        return False
    return isinstance(parsed, dict) and parsed.get("success") is True
