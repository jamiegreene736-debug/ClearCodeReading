import json

from django import template
from django.utils.safestring import mark_safe

from apps.core.bot_protection import issue_human_token

register = template.Library()


@register.inclusion_tag("includes/bot_fields.html")
def bot_fields(scope: str):
    """Honeypot plus the signed human-check token for one public form."""
    return {"human_check": issue_human_token(scope)}


@register.inclusion_tag("includes/bot_human_field.html")
def bot_human_field(scope: str):
    """Human-check token for a form that already renders its own honeypot."""
    return {"human_check": issue_human_token(scope)}


@register.simple_tag
def bot_human_json(scope: str):
    """JSON payload for a form that JavaScript assembles after the page loads."""
    return mark_safe(json.dumps({"human_check": issue_human_token(scope)}))
