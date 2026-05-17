# Clear Code Reading Admin Customizations

## `apps/users/admin.py`
```python
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

```

## `apps/schools/admin.py`
```python
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

```

## `apps/assessments/admin.py`
```python
from django.contrib import admin
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.assessments.models import Assessment
from apps.assessments.tasks import notify_assessment_review_completed


@admin.action(description="Move selected assessments to human review")
def move_to_human_review(modeladmin, request, queryset):
    queryset.update(status=Assessment.Status.HUMAN_REVIEW, updated_at=timezone.now())


@admin.action(description="Mark selected assessments completed")
def mark_completed(modeladmin, request, queryset):
    completed_at = timezone.now()
    for assessment in queryset:
        assessment.status = Assessment.Status.COMPLETED
        assessment.completed_at = assessment.completed_at or completed_at
        assessment.save(update_fields=["status", "completed_at", "updated_at"])
        notify_assessment_review_completed.delay(assessment.id)


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "child",
        "school",
        "assessment_type",
        "skill",
        "status_badge",
        "raw_score",
        "max_score",
        "scheduled_for",
        "completed_at",
    )
    list_filter = ("status", "assessment_type", "school", "skill", "scheduled_for", "completed_at", "is_deleted")
    search_fields = ("title", "child__first_name", "child__last_name", "school__name", "skill__code", "skill__name")
    autocomplete_fields = ("child", "school", "assigned_by", "skill")
    readonly_fields = ("created_at", "updated_at", "deleted_at", "review_dashboard_link")
    actions = (move_to_human_review, mark_completed)
    date_hierarchy = "created_at"

    def get_urls(self):
        return [
            path("pending-human-reviews/", self.admin_site.admin_view(self.pending_human_reviews), name="assessments_pending_human_reviews"),
        ] + super().get_urls()

    def review_dashboard_link(self, obj=None):
        url = reverse("admin:assessments_pending_human_reviews")
        return format_html('<a class="button" href="{}">Open pending human reviews dashboard</a>', url)

    review_dashboard_link.short_description = "Evaluator dashboard"

    def status_badge(self, obj):
        colors = {
            Assessment.Status.PENDING: "#6b7280",
            Assessment.Status.IN_PROGRESS: "#2563eb",
            Assessment.Status.HUMAN_REVIEW: "#d97706",
            Assessment.Status.COMPLETED: "#059669",
            Assessment.Status.ARCHIVED: "#4b5563",
        }
        return format_html(
            '<span style="background:{};color:white;padding:3px 8px;border-radius:12px;">{}</span>',
            colors.get(obj.status, "#6b7280"),
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    def pending_human_reviews(self, request):
        assessments = (
            Assessment.objects.filter(status=Assessment.Status.HUMAN_REVIEW, is_deleted=False)
            .select_related("child", "school", "skill", "assigned_by")
            .order_by("created_at")[:100]
        )
        rows = [
            "<tr><th>Assessment</th><th>Child</th><th>School</th><th>Skill</th><th>Submitted</th><th>Open</th></tr>"
        ]
        for assessment in assessments:
            change_url = reverse("admin:assessments_assessment_change", args=[assessment.id])
            rows.append(
                "<tr>"
                f"<td>{assessment.title}</td>"
                f"<td>{assessment.child}</td>"
                f"<td>{assessment.school or ''}</td>"
                f"<td>{assessment.skill or ''}</td>"
                f"<td>{assessment.updated_at:%Y-%m-%d %H:%M}</td>"
                f'<td><a href="{change_url}">Review</a></td>'
                "</tr>"
            )
        html = (
            "<html><head><title>Pending Human Reviews</title></head><body>"
            "<h1>Pending Human Reviews</h1>"
            '<p><a href="../">Back to assessments</a></p>'
            '<table border="1" cellpadding="6" cellspacing="0">'
            + "".join(rows)
            + "</table></body></html>"
        )
        return HttpResponse(html)

```

## `apps/curriculum/admin.py`
```python
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

```

## `apps/progress/admin.py`
```python
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

```

## `apps/crm/admin.py`
```python
from django.contrib import admin
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.crm.models import Lead, Opportunity


class OpportunityInline(admin.TabularInline):
    model = Opportunity
    extra = 0
    autocomplete_fields = ("school", "owner")
    fields = ("name", "stage", "value", "probability", "expected_close_date", "owner", "school")


@admin.action(description="Mark selected leads as contacted")
def mark_contacted(modeladmin, request, queryset):
    queryset.update(status=Lead.Status.CONTACTED, updated_at=timezone.now())


@admin.action(description="Mark selected leads as qualified")
def mark_qualified(modeladmin, request, queryset):
    queryset.update(status=Lead.Status.QUALIFIED, updated_at=timezone.now())


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    inlines = (OpportunityInline,)
    list_display = ("school_name", "contact_name", "contact_email", "source", "status", "assigned_to", "estimated_students", "created_at")
    list_filter = ("status", "source", "assigned_to", "is_deleted", "created_at")
    search_fields = ("school_name", "contact_name", "contact_email", "contact_phone", "notes")
    autocomplete_fields = ("assigned_to",)
    readonly_fields = ("created_at", "updated_at", "deleted_at", "pipeline_link")
    actions = (mark_contacted, mark_qualified)

    def get_urls(self):
        return [
            path("pipeline/", self.admin_site.admin_view(self.pipeline_view), name="crm_lead_pipeline"),
        ] + super().get_urls()

    def pipeline_link(self, obj=None):
        return format_html('<a class="button" href="{}">Open CRM pipeline</a>', reverse("admin:crm_lead_pipeline"))

    pipeline_link.short_description = "CRM pipeline"

    def pipeline_view(self, request):
        lead_rows = Lead.objects.filter(is_deleted=False).values("status").annotate(count=Count("id")).order_by("status")
        opportunity_rows = (
            Opportunity.objects.filter(is_deleted=False)
            .values("stage")
            .annotate(count=Count("id"), total_value=Sum("value"))
            .order_by("stage")
        )
        html = ["<html><head><title>CRM Pipeline</title></head><body><h1>CRM Lead Pipeline</h1>"]
        html.append('<p><a href="../">Back to leads</a></p>')
        html.append("<h2>Leads by Status</h2><table border='1' cellpadding='6' cellspacing='0'><tr><th>Status</th><th>Count</th></tr>")
        for row in lead_rows:
            html.append(f"<tr><td>{row['status']}</td><td>{row['count']}</td></tr>")
        html.append("</table>")
        html.append("<h2>Opportunities by Stage</h2><table border='1' cellpadding='6' cellspacing='0'><tr><th>Stage</th><th>Count</th><th>Total Value</th></tr>")
        for row in opportunity_rows:
            html.append(f"<tr><td>{row['stage']}</td><td>{row['count']}</td><td>{row['total_value'] or 0}</td></tr>")
        html.append("</table></body></html>")
        return HttpResponse("".join(html))


@admin.action(description="Mark selected opportunities as won")
def mark_won(modeladmin, request, queryset):
    queryset.update(stage=Opportunity.Stage.WON, closed_at=timezone.now(), updated_at=timezone.now())


@admin.action(description="Mark selected opportunities as lost")
def mark_lost(modeladmin, request, queryset):
    queryset.update(stage=Opportunity.Stage.LOST, closed_at=timezone.now(), updated_at=timezone.now())


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = ("name", "lead", "school", "owner", "stage", "value", "probability", "expected_close_date", "closed_at")
    list_filter = ("stage", "owner", "school", "expected_close_date", "closed_at", "is_deleted", "created_at")
    search_fields = ("name", "lead__school_name", "lead__contact_name", "school__name", "owner__email", "next_steps", "lost_reason")
    autocomplete_fields = ("lead", "school", "owner")
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = (mark_won, mark_lost)

```

## `apps/tenants/admin.py`
```python
from django.contrib import admin

from .models import Domain


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "tenant", "is_primary")
    list_filter = ("is_primary", "tenant")
    search_fields = ("domain", "tenant__name", "tenant__schema_name")
    autocomplete_fields = ("tenant",)

```
