from django.contrib import admin
from django.utils import timezone

from apps.progress.models import MasteryRecord, Progress


class MasteryRecordInline(admin.TabularInline):
    model = MasteryRecord
    extra = 0
    autocomplete_fields = ("assessment", "mastered_by")
    fields = ("skill", "assessment", "mastered_at", "mastered_by", "score", "is_deleted")
    readonly_fields = ("skill",)


@admin.action(description="Mark selected progress records as mastered")
def mark_progress_mastered(modeladmin, request, queryset):
    for progress in queryset.select_related("child", "skill"):
        progress.status = Progress.Status.MASTERED
        progress.save(update_fields=["status", "updated_at"])
        MasteryRecord.objects.get_or_create(
            child=progress.child,
            skill=progress.skill,
            progress=progress,
            defaults={"mastered_at": timezone.now(), "score": progress.current_score},
        )


@admin.register(Progress)
class ProgressAdmin(admin.ModelAdmin):
    inlines = (MasteryRecordInline,)
    list_display = ("child", "skill", "school", "status", "current_score", "target_score", "attempts", "updated_at")
    list_filter = ("status", "school", "skill__domain", "is_deleted", "created_at", "updated_at")
    search_fields = ("child__first_name", "child__last_name", "skill__code", "skill__name", "school__name")
    autocomplete_fields = ("child", "skill", "school", "last_assessment")
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = (mark_progress_mastered,)


@admin.register(MasteryRecord)
class MasteryRecordAdmin(admin.ModelAdmin):
    list_display = ("child", "skill", "progress", "assessment", "mastered_at", "mastered_by", "score", "is_deleted")
    list_filter = ("skill__domain", "mastered_at", "is_deleted", "created_at")
    search_fields = ("child__first_name", "child__last_name", "skill__code", "skill__name", "mastered_by__email")
    autocomplete_fields = ("child", "skill", "progress", "assessment", "mastered_by")
    readonly_fields = ("created_at", "updated_at", "deleted_at")
