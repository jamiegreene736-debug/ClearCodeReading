from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils import timezone

from apps.users.models import AuditLog, ChildProfile, ConsentLog, CustomUser, GuardianRelationship, Profile


class ProfileInline(admin.StackedInline):
    model = Profile
    extra = 0
    can_delete = False
    fields = ("display_name", "avatar", "timezone", "preferences", "onboarding_completed_at")


class GuardianRelationshipInline(admin.TabularInline):
    model = GuardianRelationship
    fk_name = "child"
    extra = 0
    autocomplete_fields = ("guardian",)
    fields = ("guardian", "relationship_type", "is_primary", "consent_status", "consent_expires_at", "is_deleted")
    readonly_fields = ("consent_status", "consent_expires_at")


class ConsentLogInline(admin.TabularInline):
    model = ConsentLog
    extra = 0
    autocomplete_fields = ("guardian", "child", "guardian_relationship")
    fields = ("consent_type", "status", "version", "source", "expires_at", "created_at")
    readonly_fields = ("created_at",)


@admin.action(description="Soft delete selected users")
def soft_delete_users(modeladmin, request, queryset):
    queryset.update(is_deleted=True, deleted_at=timezone.now())


@admin.action(description="Reactivate selected users")
def reactivate_users(modeladmin, request, queryset):
    queryset.update(is_active=True, is_deleted=False, deleted_at=None)


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    inlines = (ProfileInline,)
    list_display = ("email", "username", "first_name", "last_name", "role", "is_active", "is_deleted", "created_at")
    list_filter = ("role", "is_active", "is_staff", "is_superuser", "is_deleted", "created_at")
    search_fields = ("email", "username", "first_name", "last_name", "phone_number")
    ordering = ("email",)
    readonly_fields = ("created_at", "updated_at", "last_login", "date_joined")
    actions = (soft_delete_users, reactivate_users)
    fieldsets = UserAdmin.fieldsets + (
        ("Clear Code Reading", {"fields": ("role", "phone_number", "metadata", "is_deleted", "deleted_at", "created_at", "updated_at")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Clear Code Reading", {"fields": ("email", "role", "phone_number")}),
    )


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "timezone", "onboarding_completed_at", "is_deleted", "created_at")
    list_filter = ("timezone", "is_deleted", "onboarding_completed_at", "created_at")
    search_fields = ("user__email", "user__first_name", "user__last_name", "display_name")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at", "deleted_at")


@admin.action(description="Mark selected child profiles as soft deleted")
def soft_delete_children(modeladmin, request, queryset):
    queryset.update(is_deleted=True, deleted_at=timezone.now())


@admin.register(ChildProfile)
class ChildProfileAdmin(admin.ModelAdmin):
    inlines = (GuardianRelationshipInline, ConsentLogInline)
    list_display = ("first_name", "last_name", "grade_level", "school", "student_identifier", "is_deleted", "created_at")
    list_filter = ("grade_level", "school", "is_deleted", "created_at")
    search_fields = ("first_name", "last_name", "student_identifier", "school__name", "user__email")
    autocomplete_fields = ("user", "school")
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = (soft_delete_children,)


@admin.action(description="Grant consent for selected relationships")
def grant_relationship_consent(modeladmin, request, queryset):
    queryset.update(consent_status=GuardianRelationship.ConsentStatus.GRANTED, consent_expires_at=None)


@admin.action(description="Revoke consent for selected relationships")
def revoke_relationship_consent(modeladmin, request, queryset):
    queryset.update(consent_status=GuardianRelationship.ConsentStatus.REVOKED, consent_expires_at=timezone.now())


@admin.register(GuardianRelationship)
class GuardianRelationshipAdmin(admin.ModelAdmin):
    list_display = ("guardian", "child", "relationship_type", "is_primary", "consent_status", "consent_expires_at", "is_deleted")
    list_filter = ("relationship_type", "is_primary", "consent_status", "is_deleted", "created_at")
    search_fields = ("guardian__email", "guardian__first_name", "guardian__last_name", "child__first_name", "child__last_name")
    autocomplete_fields = ("guardian", "child")
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = (grant_relationship_consent, revoke_relationship_consent)


@admin.register(ConsentLog)
class ConsentLogAdmin(admin.ModelAdmin):
    list_display = ("consent_type", "status", "guardian", "child", "version", "source", "expires_at", "created_at")
    list_filter = ("consent_type", "status", "source", "created_at", "expires_at")
    search_fields = ("guardian__email", "child__first_name", "child__last_name", "version", "source")
    autocomplete_fields = ("guardian_relationship", "guardian", "child")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "entity_type", "entity_id", "actor", "created_at")
    list_filter = ("action", "entity_type", "created_at")
    search_fields = ("action", "entity_type", "entity_id", "actor__email")
    autocomplete_fields = ("actor",)
    readonly_fields = ("actor", "action", "entity_type", "entity_id", "before", "after", "metadata", "ip_address", "user_agent", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
