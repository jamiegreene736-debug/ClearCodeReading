from django.contrib import admin

from .models import Session, SessionRevision, SessionTemplate, SkillObservation


class SessionRevisionInline(admin.TabularInline):
    model = SessionRevision
    extra = 0
    can_delete = False
    fields = ("revision", "changed_by", "created_at", "snapshot")
    readonly_fields = fields


class SkillObservationInline(admin.TabularInline):
    model = SkillObservation
    extra = 0
    can_delete = False
    fields = (
        "curriculum_position",
        "accuracy_rate",
        "response_rating",
        "source_session_revision",
        "revision",
    )
    readonly_fields = fields


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    inlines = (SkillObservationInline, SessionRevisionInline)
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
        "session_template",
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
                    "session_template",
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


@admin.register(SessionTemplate)
class SessionTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "curriculum",
        "curriculum_position",
        "session_part",
        "version",
        "center",
        "is_active",
        "revision",
    )
    list_filter = ("session_part", "version", "is_active", "center", "is_deleted")
    search_fields = ("title", "curriculum__name", "curriculum_position__code")
    autocomplete_fields = (
        "center",
        "curriculum",
        "curriculum_position",
        "created_by",
        "updated_by",
    )
    readonly_fields = ("revision", "created_at", "updated_at", "deleted_at")


@admin.register(SkillObservation)
class SkillObservationAdmin(admin.ModelAdmin):
    list_display = (
        "child",
        "curriculum_position",
        "session",
        "accuracy_rate",
        "response_rating",
        "center",
        "source_session_revision",
        "revision",
    )
    list_filter = ("center", "curriculum_position__curriculum__code", "is_deleted")
    search_fields = ("child__first_name", "child__last_name", "curriculum_position__code")
    autocomplete_fields = (
        "center",
        "session",
        "child",
        "curriculum_position",
        "created_by",
        "updated_by",
    )
    readonly_fields = (
        "session",
        "child",
        "curriculum_position",
        "accuracy_rate",
        "response_rating",
        "error_pattern_tags",
        "time_signals",
        "activities",
        "item_references",
        "source_session_revision",
        "metadata",
        "created_by",
        "updated_by",
        "revision",
        "created_at",
        "updated_at",
        "deleted_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
