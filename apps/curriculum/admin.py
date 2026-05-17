from django.contrib import admin
from django.utils import timezone

from apps.curriculum.models import Lesson, Skill, TeachingAid


class TeachingAidInline(admin.TabularInline):
    model = TeachingAid
    extra = 0
    fields = ("title", "aid_type", "file", "url", "is_deleted")


@admin.action(description="Publish selected lessons")
def publish_lessons(modeladmin, request, queryset):
    queryset.update(is_published=True)


@admin.action(description="Unpublish selected lessons")
def unpublish_lessons(modeladmin, request, queryset):
    queryset.update(is_published=False)


@admin.action(description="Soft delete selected curriculum records")
def soft_delete_curriculum(modeladmin, request, queryset):
    queryset.update(is_deleted=True, deleted_at=timezone.now())


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "domain", "grade_band", "is_deleted", "created_at")
    list_filter = ("domain", "grade_band", "is_deleted", "created_at")
    search_fields = ("code", "name", "description")
    filter_horizontal = ("prerequisites",)
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = (soft_delete_curriculum,)


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    inlines = (TeachingAidInline,)
    list_display = ("title", "slug", "skill", "grade_level", "duration_minutes", "is_published", "is_deleted")
    list_filter = ("is_published", "grade_level", "skill__domain", "is_deleted", "created_at")
    search_fields = ("title", "slug", "objective", "skill__code", "skill__name")
    autocomplete_fields = ("skill",)
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = (publish_lessons, unpublish_lessons, soft_delete_curriculum)


@admin.register(TeachingAid)
class TeachingAidAdmin(admin.ModelAdmin):
    list_display = ("title", "aid_type", "lesson", "skill", "file", "url", "is_deleted")
    list_filter = ("aid_type", "skill", "is_deleted", "created_at")
    search_fields = ("title", "lesson__title", "skill__code", "skill__name")
    autocomplete_fields = ("lesson", "skill")
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = (soft_delete_curriculum,)
