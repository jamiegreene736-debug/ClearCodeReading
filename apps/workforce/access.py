from __future__ import annotations

from django.db.models import QuerySet

from apps.schools.models import SchoolMembership
from apps.users.models import CustomUser
from apps.workforce.models import Engagement, WorkforceRoleMembership


def is_global_admin(user) -> bool:
    return bool(user.is_authenticated and (user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN))


def has_workforce_role(user, payer, *roles: str) -> bool:
    if is_global_admin(user):
        return True
    return user.workforce_roles.filter(payer=payer, role__in=roles, is_active=True).exists()


def managed_center_ids(user) -> QuerySet:
    return user.school_memberships.filter(
        is_deleted=False,
        role__in=[SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN],
    ).values_list("school_id", flat=True)


def can_view_engagement(user, engagement: Engagement) -> bool:
    if engagement.worker.user_id == user.id or is_global_admin(user):
        return True
    if has_workforce_role(user, engagement.payer, *WorkforceRoleMembership.Role.values):
        return True
    return engagement.assignments.filter(center_id__in=managed_center_ids(user), is_active=True).exists()
