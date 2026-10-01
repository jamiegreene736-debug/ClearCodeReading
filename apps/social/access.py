"""Who may connect accounts and publish as ClearCode Reading."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.http.response import HttpResponseBase

from apps.users.models import CustomUser


class SocialRequest(HttpRequest):
    user: CustomUser


def can_manage_social(user: CustomUser | AnonymousUser) -> bool:
    """Super administrators only. This is the public brand, not a center tool."""
    return bool(
        user.is_authenticated
        and getattr(user, "pk", None) is not None
        and user.is_active
        and not getattr(user, "is_deleted", False)
        and (user.is_superuser or getattr(user, "role", "") == CustomUser.Role.SUPER_ADMIN)
    )


def social_editor_required(
    view: Callable[..., HttpResponseBase],
) -> Callable[..., HttpResponseBase]:
    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not can_manage_social(request.user):
            raise PermissionDenied("Only a super administrator can manage social media.")
        return view(request, *args, **kwargs)

    return wrapped
