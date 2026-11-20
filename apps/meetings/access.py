from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.meetings.models import FamilyMeeting
from apps.users.models import CustomUser, GuardianRelationship
from apps.users.portal_views import program_children, user_can_manage_instruction


def require_schedule_access(user: CustomUser) -> None:
    if not user.is_active or user.is_deleted or not (
        user_can_manage_instruction(user) or user.role in {CustomUser.Role.TEACHER, CustomUser.Role.GUARDIAN}
    ):
        raise PermissionDenied


def eligible_families() -> QuerySet[GuardianRelationship]:
    return GuardianRelationship.objects.filter(
        is_deleted=False, child__is_deleted=False, guardian__is_active=True,
        guardian__is_deleted=False, guardian__role=CustomUser.Role.GUARDIAN,
    ).exclude(consent_status__in=["expired", "revoked"]).filter(
        Q(consent_expires_at__isnull=True) | Q(consent_expires_at__gt=timezone.now())
    )


def teacher_families(teacher: CustomUser) -> QuerySet[GuardianRelationship]:
    families = eligible_families().filter(
        Q(child__learning_profile__assigned_teacher_id=teacher.pk)
        | Q(child__learning_profile__assigned_teacher_id=str(teacher.pk))
    )
    if connection.schema_name != "public":
        families = families.filter(child__school__schema_name=connection.schema_name)
    return families.select_related("child", "guardian")


def visible_meetings(user: CustomUser) -> QuerySet[FamilyMeeting]:
    require_schedule_access(user)
    meetings = FamilyMeeting.objects.select_related("teacher", "family__child", "family__guardian")
    if connection.schema_name != "public":
        meetings = meetings.filter(family__child__school__schema_name=connection.schema_name)
    if user_can_manage_instruction(user):
        return meetings.filter(family__child__in=program_children(user))
    if user.role == CustomUser.Role.TEACHER:
        return meetings.filter(teacher=user, family__in=teacher_families(user))
    return meetings.filter(family__in=eligible_families().filter(guardian=user))
