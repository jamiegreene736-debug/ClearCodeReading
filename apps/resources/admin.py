from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import admin
from django.http import HttpRequest

from apps.resources.models import Resource, Topic

if TYPE_CHECKING:
    TopicModelAdmin = admin.ModelAdmin[Topic]
    ResourceModelAdmin = admin.ModelAdmin[Resource]
else:
    TopicModelAdmin = admin.ModelAdmin
    ResourceModelAdmin = admin.ModelAdmin


@admin.register(Topic)
class TopicAdmin(TopicModelAdmin):
    search_fields = ("name",)


@admin.register(Resource)
class ResourceAdmin(ResourceModelAdmin):
    list_display = ("id", "owner", "state", "updated_at")
    readonly_fields = (
        "id",
        "owner",
        "draft",
        "live",
        "scheduled",
        "publish_at",
        "archived",
        "submitted",
        "version",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: Resource | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Resource | None = None
    ) -> bool:
        return False
