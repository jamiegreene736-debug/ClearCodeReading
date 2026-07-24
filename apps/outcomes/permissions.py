from rest_framework.permissions import BasePermission

from apps.schools.models import SchoolMembership
from apps.users.models import CustomUser


class IsLeadershipOutcomesUser(BasePermission):
    message = "You must be a leadership user to view de-identified outcomes."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or getattr(user, "role", None) == CustomUser.Role.SUPER_ADMIN:
            return True
        if getattr(user, "role", None) != CustomUser.Role.SCHOOL_ADMIN:
            return False
        admin_membership = SchoolMembership.objects.filter(
            user=user,
            is_deleted=False,
            role__in=[SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN],
        ).exists()
        designated_leadership = SchoolMembership.objects.filter(
            user=user,
            is_deleted=False,
            permissions__outcomes_reports=True,
        ).exists()
        return admin_membership or designated_leadership
