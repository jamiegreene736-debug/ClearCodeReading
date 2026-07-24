from rest_framework.permissions import BasePermission

from apps.schools.models import SchoolMembership
from apps.users.models import CustomUser


class IsLeadership(BasePermission):
    """Limit de-identified outcomes reporting to center leadership."""

    message = "Center leadership access is required for outcomes reporting."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN:
            return True
        if user.role != CustomUser.Role.SCHOOL_ADMIN:
            return False
        return SchoolMembership.objects.filter(
            user=user,
            role__in=[SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN],
            is_deleted=False,
        ).exists()

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN:
            return True
        return SchoolMembership.objects.filter(
            user=user,
            school=obj.center,
            role__in=[SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN],
            is_deleted=False,
        ).exists()
