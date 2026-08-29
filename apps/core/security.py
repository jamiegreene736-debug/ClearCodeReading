from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.csrf import csrf_failure as django_csrf_failure


def _is_login_submission(request: HttpRequest) -> bool:
    if request.method != "POST":
        return False

    try:
        match = resolve(request.path_info)
    except Resolver404:
        return False

    return match.url_name == "demo_login" or (
        match.url_name == "login" and match.namespace in {"", "admin"}
    )


def _safe_next_url(request: HttpRequest) -> str:
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if not next_url:
        return ""

    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return ""


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """Refresh stale login forms without weakening CSRF checks elsewhere."""
    if not _is_login_submission(request):
        return django_csrf_failure(request, reason=reason)

    next_url = _safe_next_url(request)
    if not next_url and request.path_info.startswith("/admin/"):
        next_url = reverse("admin:index")

    query = {"csrf": "refreshed"}
    if next_url:
        query["next"] = next_url
    return redirect(f"{reverse('login')}?{urlencode(query)}")
