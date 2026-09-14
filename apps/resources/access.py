from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import Q, QuerySet
from django.http import Http404, HttpRequest
from django.http.response import HttpResponseBase
from django.urls import reverse

from apps.resources.models import Asset, Resource
from apps.users.models import CustomUser

# These identities are seeded with public demo credentials, even when their role is admin.
DEMO_IDENTITIES = {
    "admin@clearcodereading.com",
    "parent@clearcodereading.com",
    "teacher@clearcodereading.com",
}


class EditorRequest(HttpRequest):
    user: CustomUser


def can_edit(user: CustomUser | AnonymousUser) -> bool:
    return bool(
        user.is_authenticated
        and user.pk is not None
        and user.is_active
        and not getattr(user, "is_deleted", False)
        and getattr(user, "email", "").lower() not in DEMO_IDENTITIES
        and (
            user.is_staff
            or user.is_superuser
            or getattr(user, "role", "") == "super_admin"
            or user.has_perm("resources.add_resource")
            or can_publish(user)
        )
    )


def can_publish(user: CustomUser | AnonymousUser) -> bool:
    return bool(
        user.is_authenticated
        and user.pk is not None
        and user.is_active
        and not getattr(user, "is_deleted", False)
        and getattr(user, "email", "").lower() not in DEMO_IDENTITIES
        and (
            user.is_superuser
            or getattr(user, "role", "") == "super_admin"
            or user.has_perm("resources.publish_resource")
        )
    )


def scoped_resources(user: CustomUser) -> QuerySet[Resource]:
    queryset = Resource.objects.select_related(
        "draft",
        "live",
        "scheduled",
        "draft__topic",
        "draft__asset",
        "draft__cover",
        "draft__asset__preview",
    ).defer("draft__asset__data", "draft__cover__data", "draft__asset__preview__data")
    return queryset if can_publish(user) else queryset.filter(owner=user)


def scoped_assets(user: CustomUser) -> QuerySet[Asset]:
    queryset = Asset.objects.defer("data").exclude(
        pk__in=Asset.objects.filter(preview__isnull=False).values("preview_id")
    )
    return (
        queryset
        if can_publish(user)
        else queryset.filter(
            Q(owner=user)
            | Q(file_revisions__resource__owner=user)
            | Q(cover_revisions__resource__owner=user)
        ).distinct()
    )


def public_site(
    view: Callable[..., HttpResponseBase],
) -> Callable[..., HttpResponseBase]:
    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if getattr(connection, "schema_name", "public") != "public":
            raise Http404
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        response["Vary"] = "Cookie"
        return response

    return wrapped


def editor_required(
    view: Callable[..., HttpResponseBase],
) -> Callable[..., HttpResponseBase]:
    @public_site
    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if not request.user.is_authenticated or not request.session.get(
            "resources_staff_login"
        ):
            return redirect_to_login(
                request.get_full_path(), login_url=reverse("resources:sign_in")
            )
        if not can_edit(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped
