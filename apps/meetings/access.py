from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import Exists, OuterRef, QuerySet

from apps.meetings.models import FamilyMeeting
from apps.users.models import CustomUser, GuardianRelationship
from apps.users.portal_views import user_can_manage_instruction
from apps.users.reader_board import active_families, board_readers


def require_schedule_access(user: CustomUser) -> None:
    if (
        not user.is_active
        or user.is_deleted
        or not (
            user_can_manage_instruction(user)
            or user.role in {CustomUser.Role.TEACHER, CustomUser.Role.GUARDIAN}
        )
    ):
        raise PermissionDenied


def teacher_families(teacher: CustomUser) -> QuerySet[GuardianRelationship]:
    families = active_families().filter(child__in=board_readers(teacher))
    schema = getattr(connection, "schema_name", "public")
    if schema != "public":
        families = families.filter(child__school__schema_name=schema)
    return families.select_related("child", "guardian")


def visible_meetings(user: CustomUser) -> QuerySet[FamilyMeeting]:
    require_schedule_access(user)
    meetings = FamilyMeeting.objects.select_related("teacher", "child", "parent")
    schema = getattr(connection, "schema_name", "public")
    if schema != "public":
        meetings = meetings.filter(school__schema_name=schema)
    if user_can_manage_instruction(user):
        if user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN:
            return meetings
        return meetings.filter(
            school__memberships__user=user,
            school__memberships__is_deleted=False,
            school__is_deleted=False,
        )
    family = active_families().filter(
        child_id=OuterRef("child_id"), guardian_id=OuterRef("parent_id")
    )
    if user.role == CustomUser.Role.TEACHER:
        return meetings.filter(
            Exists(family), teacher=user, child__in=board_readers(user)
        )
    return meetings.filter(Exists(family), parent=user)
