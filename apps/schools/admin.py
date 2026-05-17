from django.contrib import admin
from django.utils import timezone

from apps.schools.models import School, SchoolMembership
from apps.tenants.models import Domain


class DomainInline(admin.TabularInline):
    model = Domain
    extra = 0
    fields = ("domain", "is_primary")


class SchoolMembershipInline(admin.TabularInline):
    model = SchoolMembership
    extra = 0
    autocomplete_fields = ("user", "invited_by")
    fields = ("user", "role", "title", "joined_at", "is_deleted")


@admin.action(description="End trial for selected schools")
def end_trial(modeladmin, request, queryset):
    queryset.update(on_trial=False)


@admin.action(description="Soft delete selected schools")
def soft_delete_schools(modeladmin, request, queryset):
    queryset.update(is_deleted=True, deleted_at=timezone.now())


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    inlines = (DomainInline, SchoolMembershipInline)
    list_display = ("name", "slug", "schema_name", "district", "contact_email", "on_trial", "paid_until", "is_deleted")
    list_filter = ("on_trial", "district", "is_deleted", "created_at", "paid_until")
    search_fields = ("name", "slug", "schema_name", "district", "contact_email", "contact_phone")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = (end_trial, soft_delete_schools)


@admin.action(description="Mark selected memberships as joined now")
def mark_joined_now(modeladmin, request, queryset):
    queryset.update(joined_at=timezone.now())


@admin.register(SchoolMembership)
class SchoolMembershipAdmin(admin.ModelAdmin):
    list_display = ("school", "user", "role", "title", "joined_at", "is_deleted", "created_at")
    list_filter = ("role", "school", "is_deleted", "joined_at", "created_at")
    search_fields = ("school__name", "user__email", "user__first_name", "user__last_name", "title")
    autocomplete_fields = ("school", "user", "invited_by")
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = (mark_joined_now,)
