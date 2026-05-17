from django.contrib import admin
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.assessments.models import Assessment
from apps.assessments.tasks import notify_assessment_review_completed


@admin.action(description="Move selected assessments to human review")
def move_to_human_review(modeladmin, request, queryset):
    for assessment in queryset:
        assessment.status = Assessment.Status.HUMAN_REVIEW
        assessment.save(update_fields=["status", "updated_at"])


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
