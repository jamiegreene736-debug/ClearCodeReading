"""Bot checks for anonymous form posts.

Public form posts are rejected unless every layer that applies says the post
may continue:

* A honeypot field (``website``). People leave it blank. A client that fills
  it is discarded quietly.
* A signed human-check token rendered into the form. It is signed with the
  existing Django secret. A post that omits it, or sends an empty or forged
  value, is rejected.
* A Cloudflare Turnstile token (``cf-turnstile-response``). Siteverify must
  succeed. See ``apps.core.captcha`` for ``TURNSTILE_SITE_KEY`` and
  ``TURNSTILE_SECRET_KEY``.
* A short cache-backed burst limit per address and form. Repeated posts from
  the same network are rejected.

An empty honeypot alone is not treated as human. The post still needs a valid
human-check token and a valid CAPTCHA token. A burst of repeats is rejected
even when those look fine.

``bot_verdict`` applies the CAPTCHA requirement for every scope it protects.
Consultation booking and inventory intake call ``captcha_ok`` directly because
they keep their own rate limits.
"""

from __future__ import annotations

import sys
from enum import Enum

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.http import HttpRequest

from apps.core.captcha import (
    CAPTCHA_FIELD,
    CAPTCHA_MESSAGE,
    CAPTCHA_MOCK_TOKEN,
    captcha_ok,
)

HONEYPOT_FIELD = "website"
HUMAN_FIELD = "human_check"
HUMAN_SALT = "clearcode-public-form-human-check"
TOKEN_MAX_AGE_SECONDS = 60 * 60 * 12
BURST_WINDOW_SECONDS = 10 * 60

# Single-step marketing forms stay low. Multi-step inventory pages post once
# per section, so they get a higher ceiling. Tests can override the default.
_BURST_LIMITS = {
    "inventory": 400,
    "login": 40,
    "staff-login": 40,
    "demo-login": 40,
}
_DEFAULT_BURST_LIMIT = 200

# Longer prefixes must be listed before shorter ones that share a stem.
PUBLIC_FORM_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/crm/signup/", "signup"),
    ("/crm/survey/", "survey"),
    ("/newsletter/subscribe/", "newsletter"),
    ("/newsletter/unsubscribe/", "unsubscribe"),
    ("/book/", "consultation"),
    ("/reading-inventory/start/", "inventory-intake"),
    ("/reading-inventory/", "inventory"),
    ("/login/", "login"),
    ("/demo-login/", "demo-login"),
    ("/account/setup/", "invitation"),
    ("/resources/staff-sign-in/", "staff-login"),
    ("/resources/staff-setup/", "staff-setup"),
)

HUMAN_MESSAGE = "Please reload the page and submit the form again."
BURST_MESSAGE = (
    "Too many submissions from this network. Please wait a few minutes and try again."
)


class BotVerdict(Enum):
    HONEYPOT = "honeypot"
    HUMAN = "human"
    CAPTCHA = "captcha"
    BURST = "burst"


def bot_message(verdict: BotVerdict) -> str:
    if verdict is BotVerdict.BURST:
        return BURST_MESSAGE
    if verdict is BotVerdict.CAPTCHA:
        return CAPTCHA_MESSAGE
    return HUMAN_MESSAGE


def issue_human_token(scope: str) -> str:
    return signing.dumps({"scope": scope}, salt=HUMAN_SALT)


def human_check_ok(request: HttpRequest, scope: str) -> bool:
    token = str(request.POST.get(HUMAN_FIELD, "") or "")
    if not token.strip():
        return False
    try:
        payload = signing.loads(token, salt=HUMAN_SALT, max_age=TOKEN_MAX_AGE_SECONDS)
    except signing.BadSignature:
        return False
    return isinstance(payload, dict) and payload.get("scope") == scope


def honeypot_filled(request: HttpRequest) -> bool:
    return bool(str(request.POST.get(HONEYPOT_FIELD, "") or "").strip())


def scope_for_path(path: str) -> str:
    bare = path.split("?", 1)[0]
    if not bare.endswith("/"):
        bare = f"{bare}/"
    for prefix, scope in PUBLIC_FORM_PREFIXES:
        if bare.startswith(prefix):
            return scope
    return ""


def burst_limit(scope: str) -> int:
    configured = getattr(settings, "PUBLIC_FORM_BURST_LIMIT", None)
    if configured is not None:
        return int(configured)
    return _BURST_LIMITS.get(scope, _DEFAULT_BURST_LIMIT)


def burst_window() -> int:
    return int(getattr(settings, "PUBLIC_FORM_BURST_WINDOW", BURST_WINDOW_SECONDS))


def client_address(request: HttpRequest) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (
        forwarded.split(",")[0].strip()
        or request.META.get("REMOTE_ADDR", "")
        or "unknown"
    )


def burst_cache_key(scope: str, address: str) -> str:
    return f"public-form-burst:{scope}:{address or 'unknown'}"


def consume_burst(request: HttpRequest, scope: str) -> bool:
    """Count this attempt. Return True when it exceeds the burst limit."""
    key = burst_cache_key(scope, client_address(request))
    window = burst_window()
    if cache.add(key, 1, window):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, window)
            count = 1
    return count > burst_limit(scope)


def redirect_if_blocked(request: HttpRequest, scope: str, fallback: str):
    """Redirect a blocked login-style post. A filled honeypot is quiet."""
    from django.contrib import messages
    from django.shortcuts import redirect

    verdict = bot_verdict(request, scope)
    if verdict is None:
        return None
    if verdict is not BotVerdict.HONEYPOT:
        messages.error(request, bot_message(verdict))
    return redirect(fallback)


def inventory_bot_redirect(request: HttpRequest):
    """Stop a bot before an inventory page writes answers or a booking."""
    from django.shortcuts import redirect

    verdict = bot_verdict(request, "inventory")
    if verdict is None:
        return None
    if verdict is BotVerdict.HONEYPOT:
        return redirect(request.path)
    return redirect(f"{request.path}?form_check={verdict.value}")


def bot_verdict(request: HttpRequest, scope: str) -> BotVerdict | None:
    """Classify this post. None means the existing form handler may continue.

    A filled honeypot is reported before the human check so the historical
    quiet-discard behavior stays in place. Every rejected attempt still counts
    toward the burst limit.
    """
    if honeypot_filled(request):
        consume_burst(request, scope)
        return BotVerdict.HONEYPOT
    if not human_check_ok(request, scope):
        consume_burst(request, scope)
        return BotVerdict.HUMAN
    if not captcha_ok(request):
        consume_burst(request, scope)
        return BotVerdict.CAPTCHA
    if consume_burst(request, scope):
        return BotVerdict.BURST
    return None


_test_client_hook_installed = False


def install_test_client_human_check() -> None:
    """Stamp human-check and mock CAPTCHA tokens onto Django test-client posts.

    Production browsers get both from the rendered form. Existing tests post
    the form fields directly. Tests that need to prove a missing check can send
    ``human_check`` or ``cf-turnstile-response`` as an empty string; that value
    is left untouched. The mock CAPTCHA token is accepted only while the test
    runner has enabled the captcha mock.
    """
    from apps.core.captcha import enable_captcha_mock

    enable_captcha_mock()
    global _test_client_hook_installed
    if _test_client_hook_installed:
        return
    from django.test.client import MULTIPART_CONTENT, Client

    original = Client.post

    def post(self, path, data=None, content_type=MULTIPART_CONTENT, *args, **kwargs):
        data = _stamp_test_post(path, data, content_type)
        return original(self, path, data, content_type, *args, **kwargs)

    Client.post = post
    _test_client_hook_installed = True


def _stamp_test_post(path, data, content_type):
    media = str(content_type or "").split(";", 1)[0].strip().lower()
    if media and media not in {
        "multipart/form-data",
        "application/x-www-form-urlencoded",
    }:
        return data
    scope = scope_for_path(str(path))
    if not scope:
        return data
    if data is None:
        return {
            HUMAN_FIELD: issue_human_token(scope),
            CAPTCHA_FIELD: CAPTCHA_MOCK_TOKEN,
        }
    if isinstance(data, (str, bytes)) or not hasattr(data, "__contains__"):
        return data
    updates = {}
    if HUMAN_FIELD not in data:
        updates[HUMAN_FIELD] = issue_human_token(scope)
    if CAPTCHA_FIELD not in data:
        updates[CAPTCHA_FIELD] = CAPTCHA_MOCK_TOKEN
    if not updates:
        return data
    if type(data) is dict:
        return {**data, **updates}
    if hasattr(data, "copy") and hasattr(data, "__setitem__"):
        copied = data.copy()
        for key, value in updates.items():
            copied[key] = value
        return copied
    return data


def running_under_test_runner() -> bool:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    joined = " ".join(sys.argv)
    return command == "test" or "pytest" in joined
