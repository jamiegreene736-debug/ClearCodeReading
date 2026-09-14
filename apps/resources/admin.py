from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import admin
from django.http import HttpRequest

from apps.resources.access import can_publish
from apps.resources.models import Topic

if TYPE_CHECKING:
    TopicModelAdmin = admin.ModelAdmin[Topic]
else:
    TopicModelAdmin = admin.ModelAdmin


@admin.register(Topic)
class TopicAdmin(TopicModelAdmin):
    search_fields = ("name",)

    def has_module_permission(self, request: HttpRequest) -> bool:
        return bool(
            getattr(request, "session", {}).get("resources_staff_login")
            and can_publish(request.user)
        )

    def has_view_permission(
        self, request: HttpRequest, obj: Topic | None = None
    ) -> bool:
        return self.has_module_permission(request)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return self.has_module_permission(request)

    def has_change_permission(
        self, request: HttpRequest, obj: Topic | None = None
    ) -> bool:
        return self.has_module_permission(request)

    def has_delete_permission(
        self, request: HttpRequest, obj: Topic | None = None
    ) -> bool:
        return self.has_module_permission(request)
