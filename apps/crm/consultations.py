"""Host selection for the CRM consultation calendar."""

from django.conf import settings
from django.db.models import QuerySet
from django.http import Http404, HttpRequest

from apps.crm.access import crm_owner_queryset
from apps.users.models import CustomUser


def default_consultation_host() -> CustomUser | None:
    hosts = crm_owner_queryset()
    email = getattr(settings, "CRM_DEFAULT_CONSULTATION_HOST_EMAIL", "")
    candidates = (
        hosts.filter(email__iexact=email)
        if email
        else hosts.filter(first_name__iexact="Bethany", last_name__iexact="Fleming")
    )
    matches = list(candidates[:2])
    return matches[0] if len(matches) == 1 else None


def selected_consultation_host(request: HttpRequest) -> CustomUser | None:
    value = request.GET.get("host")
    if value is None:
        return default_consultation_host()
    if not value.isdecimal():
        raise Http404("Host unavailable")
    host = crm_owner_queryset().filter(pk=value).first()
    if host is None:
        raise Http404("Host unavailable")
    return host


def can_manage_team_availability(user: CustomUser) -> bool:
    return user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN


def editable_hosts(user: CustomUser) -> QuerySet[CustomUser]:
    hosts = crm_owner_queryset()
    return hosts if can_manage_team_availability(user) else hosts.filter(pk=user.pk)
