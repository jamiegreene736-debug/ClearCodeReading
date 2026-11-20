from typing import TYPE_CHECKING

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.meetings.access import visible_meetings
from apps.meetings.models import FamilyMeeting
from apps.users.models import CustomUser
from apps.users.portal_views import user_can_manage_instruction

if TYPE_CHECKING:
    MeetingAdminBase = admin.ModelAdmin[FamilyMeeting]
else:
    MeetingAdminBase = admin.ModelAdmin


@admin.register(FamilyMeeting)
class FamilyMeetingAdmin(MeetingAdminBase):
    list_display = (
        "starts_at",
        "ends_at",
        "child",
        "teacher",
        "parent",
        "location",
        "cancelled",
    )
    list_filter = ("cancelled",)
    search_fields = (
        "teacher__email",
        "parent__email",
        "child__first_name",
        "child__last_name",
    )

    def has_view_permission(
        self, request: HttpRequest, obj: FamilyMeeting | None = None
    ) -> bool:
        return bool(
            isinstance(request.user, CustomUser)
            and user_can_manage_instruction(request.user)
            and super().has_view_permission(request, obj)
        )

    def get_queryset(self, request: HttpRequest) -> QuerySet[FamilyMeeting]:
        if isinstance(request.user, CustomUser) and self.has_view_permission(request):
            return visible_meetings(request.user)
        return FamilyMeeting.objects.none()

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: FamilyMeeting | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: FamilyMeeting | None = None
    ) -> bool:
        return False
