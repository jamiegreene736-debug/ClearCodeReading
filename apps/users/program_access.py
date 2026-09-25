"""Shared access rules for instructional program pages."""

from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.users.models import ChildProfile, CustomUser, GuardianRelationship


def role_flags(user) -> dict[str, bool]:
    return {
        "is_admin": user.role in {CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN} or user.is_superuser,
        "is_parent": user.role == CustomUser.Role.GUARDIAN,
        "is_teacher": user.role == CustomUser.Role.TEACHER,
        "is_child": user.role == CustomUser.Role.STUDENT,
    }


def can_manage_invitations(user) -> bool:
    return bool(user.is_active and not user.is_deleted and (user.can_manage_crm_users or user.role == CustomUser.Role.SCHOOL_ADMIN))


def children_for_portal_user(user) -> list[ChildProfile]:
    flags = role_flags(user)
    if flags["is_parent"]:
        relationships = GuardianRelationship.objects.filter(
            guardian=user,
            is_deleted=False,
            child__is_deleted=False,
        ).select_related("child")
        return [relationship.child for relationship in relationships]
    if flags["is_child"]:
        child_profile = getattr(user, "child_profile", None)
        return [child_profile] if child_profile and not child_profile.is_deleted else []
    if flags["is_teacher"]:
        assigned_children = []
        for child in ChildProfile.objects.filter(is_deleted=False).order_by("last_name", "first_name"):
            if str((child.learning_profile or {}).get("assigned_teacher_id")) == str(user.id):
                assigned_children.append(child)
        return assigned_children

    queryset = ChildProfile.objects.filter(is_deleted=False)
    if not user.is_superuser and user.role != CustomUser.Role.SUPER_ADMIN:
        queryset = queryset.filter(
            school__memberships__user=user,
            school__memberships__is_deleted=False,
        )
    return list(queryset.distinct().order_by("last_name", "first_name"))


def portal_return(request, fallback_name: str) -> str:
    fallback = reverse(fallback_name)
    candidate = (request.POST.get("next") or "").strip()
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}):
        return candidate
    return fallback
