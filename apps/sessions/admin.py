from django.contrib import admin

from .models import Session, SessionRevision


class SessionRevisionInline(admin.TabularInline):
    model = SessionRevision
    extra = 0
    can_delete = False
    fields = ("revision", "changed_by", "created_at", "snapshot")
    readonly_fields = fields


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    inlines = (SessionRevisionInline,)
    list_display = (
        "child",
        "specialist",
        "curriculum_position",
        "intervention_part",
        "scheduled_start",
        "status",
        "accuracy_rate",
        "accuracy_numerator",
        "accuracy_denominator",
        "revision",
    )
    list_filter = ("status", "intervention_part", "center", "is_deleted")
    search_fields = ("child__first_name", "child__last_name", "specialist__email", "notes")
    autocomplete_fields = (
        "center",
        "child",
        "specialist",
        "curriculum_position",
        "targeted_positions",
        "created_by",
        "updated_by",
    )
    readonly_fields = ("revision", "created_at", "updated_at", "deleted_at")
    fieldsets = (
        (
            "Fast session capture",
            {
                "fields": (
                    "center",
                    "child",
                    "specialist",
                    "curriculum_position",
                    "targeted_positions",
                    "intervention_part",
                    "status",
                    "scheduled_start",
                    ("started_at", "ended_at"),
                    ("accuracy_numerator", "accuracy_denominator", "accuracy_rate"),
                    "activities_completed",
                    "item_sets",
                    "time_to_mastery_signals",
                    "error_patterns",
                    "behavioral_observations",
                    "next_session_direction",
                    "home_practice_suggestion",
                )
            },
        ),
        ("Supplemental notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Audit",
            {
                "fields": ("created_by", "updated_by", "revision", "created_at", "updated_at", "deleted_at"),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(SessionRevision)
class SessionRevisionAdmin(admin.ModelAdmin):
    list_display = ("session", "revision", "center", "changed_by", "created_at")
    list_filter = ("center", "created_at")
    search_fields = ("session__child__first_name", "session__child__last_name")
    autocomplete_fields = ("session", "center", "changed_by")
    readonly_fields = ("session", "center", "revision", "changed_by", "snapshot", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
