"""Teacher choices for family onboarding and the existing reader assignment format."""

from typing import Any

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.users.models import CustomUser, Profile


def teaching_accounts() -> QuerySet[CustomUser]:
    return CustomUser.objects.filter(
        Q(role=CustomUser.Role.TEACHER)
        | Q(
            role__in=[CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN],
            profile__teaches_families=True,
            profile__is_deleted=False,
        )
    )


def family_teacher_choices(actor: CustomUser) -> QuerySet[CustomUser]:
    choices = (
        teaching_accounts()
        .filter(is_active=True, is_deleted=False)
        .exclude(
            Q(email__iexact="teacher@clearcodereading.com")
            | Q(email__iexact="admin@clearcodereading.com")
            | Q(metadata__contains={"demo": True})
        )
    )
    if not actor.can_manage_crm_users:
        schools = actor.school_memberships.filter(
            is_deleted=False, school__is_deleted=False
        ).values("school_id")
        choices = choices.filter(
            school_memberships__school__in=schools, school_memberships__is_deleted=False
        ).distinct()
    return choices


def reader_teacher_fields(teacher: CustomUser) -> dict[str, Any]:
    return {
        "assigned_teacher_id": teacher.pk,
        "assigned_teacher_name": str(teacher),
        "assigned_teacher_email": teacher.email,
        "assigned_at": timezone.now().isoformat(),
    }


def current_family_teacher(parent: CustomUser) -> CustomUser | None:
    profile: Profile | None = getattr(parent, "profile", None)
    if not profile or profile.is_deleted:
        return None
    teacher = profile.family_teacher
    if (
        teacher
        and teacher.is_active
        and not teacher.is_deleted
        and teacher.is_family_teacher
    ):
        return teacher
    return None


def inherited_teacher_fields(parent: CustomUser) -> dict[str, Any]:
    teacher = current_family_teacher(parent)
    return reader_teacher_fields(teacher) if teacher else {}
