from typing import cast

from django import template
from django.http import HttpRequest
from django.template import Context

from apps.social.models import SocialPost
from apps.social.navigation import navigation

register = template.Library()


@register.simple_tag(takes_context=True)
def social_navigation(context: Context, post: SocialPost | str = "") -> dict[str, str]:
    return navigation(
        cast(HttpRequest, context["request"]),
        post if isinstance(post, SocialPost) else None,
    )
