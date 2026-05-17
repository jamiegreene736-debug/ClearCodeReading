from django.utils import timezone
from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.schools.models import SchoolMembership
from apps.users.models import CustomUser, GuardianRelationship


EVALUATOR_ROLES = {
    CustomUser.Role.SUPER_ADMIN,
    CustomUser.Role.SCHOOL_ADMIN,
    CustomUser.Role.TEACHER,
}

SCHOOL_ADMIN_ROLES = {
    CustomUser.Role.SUPER_ADMIN,
    CustomUser.Role.SCHOOL_ADMIN,
}

CONSENT_REQUIRED_TYPES = {"assessment", "data_processing", "school_sharing"}


def get_child_from_obj(obj):
    if hasattr(obj, "child"):
        return obj.child
    if hasattr(obj, "child_profile"):
        return obj.child_profile
    return obj


def has_school_membership(user, school, roles=None):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "role", None) == CustomUser.Role.SUPER_ADMIN:
        return True
    if school is None:
        return False

    queryset = SchoolMembership.objects.filter(user=user, school=school, is_deleted=False)
    if roles is not None:
        queryset = queryset.filter(role__in=roles)
    return queryset.exists()


def is_parent_of_child(user, child):
    if not user or not user.is_authenticated or child is None:
        return False
    return GuardianRelationship.objects.filter(
        guardian=user,
        child=child,
        is_deleted=False,
        consent_status=GuardianRelationship.ConsentStatus.GRANTED,
    ).filter(
        models_consent_filter()
    ).exists()


def models_consent_filter():
    from django.db.models import Q

    return Q(consent_expires_at__isnull=True) | Q(consent_expires_at__gt=timezone.now())


def has_coppa_consent(child, consent_types=None):
    if child is None:
        return False

    now = timezone.now()
    relationships = GuardianRelationship.objects.filter(
        child=child,
        is_deleted=False,
        consent_status=GuardianRelationship.ConsentStatus.GRANTED,
    ).filter(
        models_consent_filter()
    )

    if not relationships.exists():
        return False

    consent_types = set(consent_types or CONSENT_REQUIRED_TYPES)
    if not consent_types:
        return True

    from apps.users.models import ConsentLog

    for consent_type in consent_types:
        if not ConsentLog.objects.filter(
            child=child,
            consent_type=consent_type,
            status=ConsentLog.Status.GRANTED,
        ).filter(
            models_consent_filter_for_log(now)
        ).exists():
            return False

    return True


def models_consent_filter_for_log(now):
    from django.db.models import Q

    return Q(expires_at__isnull=True) | Q(expires_at__gt=now)


def user_can_evaluate_child(user, child):
    if not user or not user.is_authenticated or child is None:
        return False
    if user.is_superuser or getattr(user, "role", None) in EVALUATOR_ROLES:
        return child.school is None or has_school_membership(user, child.school)
    return False


class IsParentOfChild(BasePermission):
    message = "You must be an approved guardian for this child."

    def has_object_permission(self, request, view, obj):
        child = get_child_from_obj(obj)
        return is_parent_of_child(request.user, child)


class IsEvaluator(BasePermission):
    message = "You must be an evaluator assigned to this child's school."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and getattr(user, "role", None) in EVALUATOR_ROLES)

    def has_object_permission(self, request, view, obj):
        child = get_child_from_obj(obj)
        return user_can_evaluate_child(request.user, child)


class IsSchoolAdmin(BasePermission):
    message = "You must be a school administrator."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and getattr(user, "role", None) in SCHOOL_ADMIN_ROLES)

    def has_object_permission(self, request, view, obj):
        school = getattr(obj, "school", obj)
        return has_school_membership(
            request.user,
            school,
            roles=[SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN],
        )


class COPPAConsentRequired(BasePermission):
    message = "COPPA consent is required before accessing or modifying this child's records."

    def has_object_permission(self, request, view, obj):
        child = get_child_from_obj(obj)
        if request.method in SAFE_METHODS and is_parent_of_child(request.user, child):
            return True
        return has_coppa_consent(child)
