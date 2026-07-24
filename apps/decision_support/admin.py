from django.contrib import admin

from .models import Flag, Milestone, OutcomeAggregate, Prediction


@admin.register(Flag)
class FlagAdmin(admin.ModelAdmin):
    list_display = ("child", "code", "center", "status", "raised_at", "routed_to")
    list_filter = ("code", "status", "center", "is_deleted")
    search_fields = ("child__first_name", "child__last_name", "model_or_rule_version")
    autocomplete_fields = (
        "center",
        "child",
        "curriculum_position",
        "routed_to",
        "acknowledged_by",
        "created_by",
        "updated_by",
    )
    raw_id_fields = ("related_session",)
    readonly_fields = ("revision", "created_at", "updated_at", "deleted_at")


@admin.register(Prediction)
class PredictionAdmin(admin.ModelAdmin):
    list_display = ("child", "center", "target_milestone", "target_position", "confidence", "generated_at")
    list_filter = ("center", "model_version", "generated_at", "is_deleted")
    search_fields = ("child__first_name", "child__last_name", "model_version")
    autocomplete_fields = (
        "center",
        "child",
        "target_milestone",
        "target_position",
        "created_by",
        "updated_by",
    )
    readonly_fields = ("revision", "created_at", "updated_at", "deleted_at")


@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ("child", "center", "status", "target_date", "achieved_date")
    list_filter = ("center", "status", "target_date", "is_deleted")
    search_fields = ("child__first_name", "child__last_name", "definition", "skill_band")
    autocomplete_fields = (
        "center",
        "child",
        "curriculum_position",
        "created_by",
        "updated_by",
    )
    readonly_fields = ("revision", "created_at", "updated_at", "deleted_at")


@admin.register(OutcomeAggregate)
class OutcomeAggregateAdmin(admin.ModelAdmin):
    list_display = (
        "center",
        "dimension",
        "dimension_value",
        "metric_name",
        "value",
        "cohort_size",
        "period_end",
    )
    list_filter = ("center", "dimension", "metric_name", "period_end")
    search_fields = ("dimension_value", "metric_name")
    readonly_fields = (
        "center",
        "dimension",
        "dimension_value",
        "metric_name",
        "value",
        "cohort_size",
        "period_start",
        "period_end",
        "generated_at",
        "created_at",
        "updated_at",
        "is_deleted",
        "deleted_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
