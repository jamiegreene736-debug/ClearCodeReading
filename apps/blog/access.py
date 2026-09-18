"""Who may write and publish blog posts from the portal."""

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


class EditorRequest(HttpRequest):
    user: CustomUser


def can_manage_blog(user: CustomUser | AnonymousUser) -> bool:
    """Staff, superusers, super admins, or anyone granted the blog change permission."""
    return bool(
        user.is_authenticated
        and user.pk is not None
        and user.is_active
        and not getattr(user, "is_deleted", False)
        and (
            user.is_superuser
            or user.is_staff
            or getattr(user, "role", "") == CustomUser.Role.SUPER_ADMIN
            or user.has_perm("blog.change_blogpost")
        )
    )


def blog_editor_required(
    view: Callable[..., HttpResponseBase],
) -> Callable[..., HttpResponseBase]:
    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not can_manage_blog(request.user):
            raise PermissionDenied("Only ClearCode editors can manage blog posts.")
        return view(request, *args, **kwargs)

    return wrapped
