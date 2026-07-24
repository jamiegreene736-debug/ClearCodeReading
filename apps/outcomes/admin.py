from django.contrib import admin

from apps.outcomes.models import DeIdentifiedOutcomeSnapshot


@admin.register(DeIdentifiedOutcomeSnapshot)
class DeIdentifiedOutcomeSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "center_key",
        "methodology",
        "grade_band",
        "window_type",
        "window_start",
        "window_end",
        "aggregate_version",
        "generated_at",
    )
    list_filter = ("window_type", "methodology", "grade_band", "aggregate_version", "generated_at")
    search_fields = ("center_key", "methodology", "grade_band")
    readonly_fields = (
        "center",
        "center_key",
        "methodology",
        "grade_band",
        "window_type",
        "window_start",
        "window_end",
        "metric_scope",
        "aggregate_version",
        "metrics",
        "source_counts",
        "generated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
