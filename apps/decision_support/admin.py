from django.contrib import admin, messages
from django.utils import timezone

from apps.decision_support.models import GrowthFlag, MilestonePrediction


@admin.action(description="Acknowledge selected growth flags")
def acknowledge_growth_flags(modeladmin, request, queryset):
    count = 0
    for flag in queryset.exclude(status=GrowthFlag.Status.RESOLVED):
        flag.status = GrowthFlag.Status.ACKNOWLEDGED
        flag.acknowledged_at = timezone.now()
        flag.acknowledged_by = request.user
        flag.updated_by = request.user
        flag.save(
            update_fields=["status", "acknowledged_at", "acknowledged_by", "updated_by", "updated_at"]
        )
        count += 1
    modeladmin.message_user(request, f"Acknowledged {count} growth flag(s).", messages.SUCCESS)


@admin.register(GrowthFlag)
class GrowthFlagAdmin(admin.ModelAdmin):
    list_display = (
        "child",
        "flag_code",
        "severity",
        "position",
        "status",
        "opened_at",
        "center",
    )
    list_filter = ("status", "severity", "flag_code", "center", "opened_at")
    search_fields = ("child__first_name", "child__last_name", "position__code", "explanation")
    autocomplete_fields = (
        "center",
        "child",
        "trigger_session",
        "position",
        "routed_to",
        "acknowledged_by",
        "resolved_by",
        "created_by",
        "updated_by",
    )
    readonly_fields = (
        "flag_code",
        "severity",
        "evidence_snapshot",
        "explanation",
        "advisory_recommendation",
        "opened_at",
        "revision",
        "created_at",
        "updated_at",
    )
    actions = (acknowledge_growth_flags,)


@admin.register(MilestonePrediction)
class MilestonePredictionAdmin(admin.ModelAdmin):
    list_display = (
        "child",
        "target_label",
        "predicted_sessions",
        "predicted_date",
        "confidence",
        "is_current",
        "generated_at",
        "center",
    )
    list_filter = ("confidence", "is_current", "center", "generated_at")
    search_fields = ("child__first_name", "child__last_name", "target_label", "explanation")
    autocomplete_fields = (
        "center",
        "child",
        "placement",
        "target_position",
        "created_by",
        "updated_by",
    )
    readonly_fields = (
        "predicted_sessions",
        "predicted_date",
        "lower_bound_sessions",
        "upper_bound_sessions",
        "confidence",
        "evidence_summary",
        "explanation",
        "parent_timeline",
        "disclaimer",
        "engine_version",
        "generated_at",
        "revision",
        "created_at",
        "updated_at",
    )
