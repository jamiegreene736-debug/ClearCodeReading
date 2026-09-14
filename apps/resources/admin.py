from django.contrib import admin

from apps.resources.models import Resource, Topic


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
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

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
