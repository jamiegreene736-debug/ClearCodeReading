import json

from django import template
from django.utils.safestring import mark_safe

from apps.core.bot_protection import issue_human_token
from apps.core.captcha import turnstile_site_key

register = template.Library()


def _bot_context(scope: str) -> dict[str, str]:
    return {
        "human_check": issue_human_token(scope),
        "turnstile_site_key": turnstile_site_key(),
    }


@register.inclusion_tag("includes/bot_fields.html")
def bot_fields(scope: str):
    """Honeypot, signed human-check token, and Turnstile widget."""
    return _bot_context(scope)


@register.inclusion_tag("includes/bot_human_field.html")
def bot_human_field(scope: str):
    """Human-check token and Turnstile widget for a form with its own honeypot."""
    return _bot_context(scope)


@register.simple_tag
def bot_human_json(scope: str):
    """JSON payload for a form that JavaScript assembles after the page loads."""
    payload = json.dumps(
        {
            "human_check": issue_human_token(scope),
            "turnstile_site_key": turnstile_site_key(),
        }
    ).replace("<", "\\u003c")
    return mark_safe(payload)
