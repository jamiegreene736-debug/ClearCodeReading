import json

from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.assessments.models import Assessment, AssessmentQuestion, AssessmentResult, ChildAssessmentResponse, QuestionOption
from apps.assessments.services import compute_and_persist_assessment_result
from apps.assessments.tasks import notify_assessment_review_completed
from apps.notifications.tasks import notify_evaluator_assessment_human_review


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


@admin.action(description="Recalculate reading survey results")
def recalculate_reading_results(modeladmin, request, queryset):
    recalculated = 0
    for assessment in queryset:
        compute_and_persist_assessment_result(assessment.id)
        recalculated += 1
    modeladmin.message_user(request, f"Recalculated {recalculated} reading survey result(s).", messages.SUCCESS)


@admin.action(description="Notify evaluators for human review")
def notify_evaluators(modeladmin, request, queryset):
    queued = 0
    for assessment in queryset:
        notify_evaluator_assessment_human_review.delay(assessment.id)
        queued += 1
    modeladmin.message_user(request, f"Queued evaluator notifications for {queued} assessment(s).", messages.SUCCESS)


class AssessmentResultInline(admin.StackedInline):
    model = AssessmentResult
    extra = 0
    can_delete = False
    fields = (
        "reading_age",
        "grade_equivalent",
        "final_message",
        "overall_score_display",
        "category_breakdown_display",
        "strengths_display",
        "growth_areas_display",
        "teacher_summary",
        "evaluator_notes",
    )
    readonly_fields = (
        "reading_age",
        "grade_equivalent",
        "final_message",
        "overall_score_display",
        "category_breakdown_display",
        "strengths_display",
        "growth_areas_display",
        "teacher_summary",
    )

    def final_message(self, obj):
        if obj is None:
            return "Not scored"
        return obj.final_scores.get("final_message", f"You are reading at an {obj.reading_age}-year-old level")

    def overall_score_display(self, obj):
        if obj is None:
            return "Not scored"
        return obj.final_scores.get("overall_score", "Not scored")

    overall_score_display.short_description = "Overall score"

    def category_breakdown_display(self, obj):
        if obj is None:
            return "No KPI data yet"
        return pretty_json(obj.category_breakdown)

    category_breakdown_display.short_description = "KPI breakdown"

    def strengths_display(self, obj):
        if obj is None:
            return "None yet"
        return list_preview(obj.strengths)

    strengths_display.short_description = "Strengths"

    def growth_areas_display(self, obj):
        if obj is None:
            return "None yet"
        return list_preview(obj.growth_areas)

    growth_areas_display.short_description = "Growth areas"


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    inlines = (AssessmentResultInline,)
    list_display = (
        "title",
        "child",
        "school",
        "assessment_type",
        "skill",
        "status_badge",
        "reading_age_badge",
        "overall_score_display",
        "grade_equivalent",
        "strengths_preview",
        "growth_areas_preview",
        "scheduled_for",
        "survey_completed_at",
        "completed_at",
    )
    list_filter = (
        "status",
        "assessment_type",
        "school",
        "skill",
        "reading_age",
        "overall_score",
        "scheduled_for",
        "survey_completed_at",
        "completed_at",
        "is_deleted",
    )
    search_fields = (
        "title",
        "child__first_name",
        "child__last_name",
        "school__name",
        "skill__code",
        "skill__name",
        "result__teacher_summary",
        "result__evaluator_notes",
    )
    autocomplete_fields = ("child", "school", "assigned_by", "skill")
    readonly_fields = (
        "created_at",
        "updated_at",
        "deleted_at",
        "review_dashboard_link",
        "completed_surveys_link",
        "kpi_breakdown_display",
        "strengths_display",
        "growth_areas_display",
        "final_message",
    )
    fieldsets = (
        ("Assessment", {"fields": ("title", "child", "school", "assigned_by", "assessment_type", "status", "skill")}),
        ("Survey Result", {"fields": ("reading_age", "overall_score", "survey_completed_at", "final_message", "kpi_breakdown_display", "strengths_display", "growth_areas_display")}),
        ("Timing", {"fields": ("scheduled_for", "started_at", "completed_at")}),
        ("Scores", {"fields": ("raw_score", "max_score", "percentile", "scoring", "recommendations")}),
        ("Metadata", {"fields": ("responses", "metadata", "is_deleted", "deleted_at", "created_at", "updated_at")}),
        ("Evaluator Dashboards", {"fields": ("review_dashboard_link", "completed_surveys_link")}),
    )
    actions = (move_to_human_review, mark_completed, recalculate_reading_results, notify_evaluators)
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("child", "school", "skill", "assigned_by", "result")

    def get_urls(self):
        return [
            path("pending-human-reviews/", self.admin_site.admin_view(self.pending_human_reviews), name="assessments_pending_human_reviews"),
            path("completed-surveys/", self.admin_site.admin_view(self.completed_surveys), name="assessments_completed_surveys"),
        ] + super().get_urls()

    def review_dashboard_link(self, obj=None):
        url = reverse("admin:assessments_pending_human_reviews")
        return format_html('<a class="button" href="{}">Open pending human reviews dashboard</a>', url)

    review_dashboard_link.short_description = "Evaluator dashboard"

    def completed_surveys_link(self, obj=None):
        url = reverse("admin:assessments_completed_surveys")
        return format_html('<a class="button" href="{}">Open completed surveys dashboard</a>', url)

    completed_surveys_link.short_description = "Completed surveys"

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

    def reading_age_badge(self, obj):
        reading_age = obj.reading_age or getattr(getattr(obj, "result", None), "reading_age", None)
        if reading_age is None:
            return "Not scored"
        return format_html(
            '<strong style="color:#00B8A9;font-size:14px;">{} years</strong>',
            reading_age,
        )

    reading_age_badge.short_description = "Reading Age"
    reading_age_badge.admin_order_field = "reading_age"

    def overall_score_display(self, obj):
        if obj.overall_score is not None:
            return f"{obj.overall_score}%"
        if hasattr(obj, "result"):
            score = obj.result.final_scores.get("overall_score")
            return f"{score}%" if score is not None else "Not scored"
        return "Not scored"

    overall_score_display.short_description = "Overall"
    overall_score_display.admin_order_field = "overall_score"

    def grade_equivalent(self, obj):
        return getattr(getattr(obj, "result", None), "grade_equivalent", "") or "Not scored"

    grade_equivalent.short_description = "Grade"

    def strengths_preview(self, obj):
        result = getattr(obj, "result", None)
        return list_preview(result.strengths if result else [])

    strengths_preview.short_description = "Strengths"

    def growth_areas_preview(self, obj):
        result = getattr(obj, "result", None)
        return list_preview(result.growth_areas if result else [])

    growth_areas_preview.short_description = "Growth Areas"

    def kpi_breakdown_display(self, obj):
        result = getattr(obj, "result", None)
        return pretty_json(result.category_breakdown if result else {})

    kpi_breakdown_display.short_description = "Full KPI breakdown"

    def strengths_display(self, obj):
        result = getattr(obj, "result", None)
        return list_preview(result.strengths if result else [])

    strengths_display.short_description = "Strengths"

    def growth_areas_display(self, obj):
        result = getattr(obj, "result", None)
        return list_preview(result.growth_areas if result else [])

    growth_areas_display.short_description = "Growth areas"

    def final_message(self, obj):
        result = getattr(obj, "result", None)
        if not result:
            return "Not scored"
        return result.final_scores.get("final_message", f"You are reading at an {result.reading_age}-year-old level")

    final_message.short_description = "Final reading level message"

    def pending_human_reviews(self, request):
        assessments = (
            Assessment.objects.filter(status=Assessment.Status.HUMAN_REVIEW, is_deleted=False)
            .select_related("child", "school", "skill", "assigned_by", "result")
            .order_by("created_at")[:100]
        )
        rows = [
            "<tr><th>Assessment</th><th>Child</th><th>School</th><th>Reading Age</th><th>Overall</th><th>Growth Areas</th><th>Submitted</th><th>Open</th></tr>"
        ]
        for assessment in assessments:
            change_url = reverse("admin:assessments_assessment_change", args=[assessment.id])
            result = getattr(assessment, "result", None)
            rows.append(
                "<tr>"
                f"<td>{assessment.title}</td>"
                f"<td>{assessment.child}</td>"
                f"<td>{assessment.school or ''}</td>"
                f"<td><strong>{assessment.reading_age or (result.reading_age if result else 'Not scored')}</strong></td>"
                f"<td>{assessment.overall_score or (result.final_scores.get('overall_score') if result else 'Not scored')}</td>"
                f"<td>{', '.join(result.growth_areas[:3]) if result else ''}</td>"
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

    def completed_surveys(self, request):
        assessments = (
            Assessment.objects.filter(status=Assessment.Status.COMPLETED, is_deleted=False)
            .select_related("child", "school", "skill", "assigned_by", "result")
            .order_by("-completed_at", "-survey_completed_at")[:200]
        )
        rows = [
            "<tr><th>Assessment</th><th>Child</th><th>School</th><th>Reading Age</th><th>Grade</th><th>Overall</th><th>Strengths</th><th>Growth Areas</th><th>Completed</th><th>Open</th></tr>"
        ]
        for assessment in assessments:
            result = getattr(assessment, "result", None)
            change_url = reverse("admin:assessments_assessment_change", args=[assessment.id])
            completed_cell = f"<td>{assessment.completed_at:%Y-%m-%d %H:%M}</td>" if assessment.completed_at else "<td></td>"
            rows.append(
                "<tr>"
                f"<td>{assessment.title}</td>"
                f"<td>{assessment.child}</td>"
                f"<td>{assessment.school or ''}</td>"
                f"<td><strong style='color:#00B8A9'>{assessment.reading_age or (result.reading_age if result else 'Not scored')}</strong></td>"
                f"<td>{result.grade_equivalent if result else ''}</td>"
                f"<td>{assessment.overall_score or (result.final_scores.get('overall_score') if result else 'Not scored')}</td>"
                f"<td>{', '.join(result.strengths[:3]) if result else ''}</td>"
                f"<td>{', '.join(result.growth_areas[:3]) if result else ''}</td>"
                f"{completed_cell}"
                f'<td><a href="{change_url}">Open</a></td>'
                "</tr>"
            )
        html = (
            "<html><head><title>Completed Reading Surveys</title></head><body>"
            "<h1>Completed Reading Surveys</h1>"
            '<p><a href="../">Back to assessments</a></p>'
            '<table border="1" cellpadding="6" cellspacing="0">'
            + "".join(rows)
            + "</table></body></html>"
        )
        return HttpResponse(html)


class QuestionOptionInline(admin.TabularInline):
    model = QuestionOption
    extra = 0
    fields = ("label", "value", "is_correct", "score_value", "sort_order", "is_deleted")


@admin.register(AssessmentQuestion)
class AssessmentQuestionAdmin(admin.ModelAdmin):
    inlines = (QuestionOptionInline,)
    list_display = ("category", "difficulty", "question_type", "question_preview", "sort_order", "is_active", "is_deleted")
    list_filter = ("category", "difficulty", "question_type", "is_active", "is_deleted", "created_at")
    search_fields = ("question_text", "correct_answer", "options")
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = ("activate_questions", "deactivate_questions")

    def question_preview(self, obj):
        return obj.question_text[:80]

    question_preview.short_description = "Question"

    @admin.action(description="Activate selected questions")
    def activate_questions(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Deactivate selected questions")
    def deactivate_questions(self, request, queryset):
        queryset.update(is_active=False)


@admin.register(QuestionOption)
class QuestionOptionAdmin(admin.ModelAdmin):
    list_display = ("question", "label", "value", "is_correct", "score_value", "sort_order", "is_deleted")
    list_filter = ("is_correct", "question__category", "is_deleted", "created_at")
    search_fields = ("label", "value", "question__question_text")
    autocomplete_fields = ("question",)
    readonly_fields = ("created_at", "updated_at", "deleted_at")


@admin.register(ChildAssessmentResponse)
class ChildAssessmentResponseAdmin(admin.ModelAdmin):
    list_display = ("child", "assessment", "question", "selected_option", "is_correct", "score_value", "time_taken", "created_at")
    list_filter = ("question__category", "is_correct", "is_deleted", "created_at")
    search_fields = ("child__first_name", "child__last_name", "question__question_text")
    autocomplete_fields = ("assessment", "child", "question", "selected_option")
    readonly_fields = ("created_at", "updated_at", "deleted_at")


@admin.register(AssessmentResult)
class AssessmentResultAdmin(admin.ModelAdmin):
    list_display = (
        "assessment",
        "child",
        "school",
        "reading_age_badge",
        "grade_equivalent",
        "overall_score_display",
        "strengths_preview",
        "growth_areas_preview",
        "has_evaluator_notes",
        "created_at",
        "is_deleted",
    )
    list_filter = ("grade_equivalent", "reading_age", "is_deleted", "created_at")
    search_fields = (
        "assessment__title",
        "assessment__child__first_name",
        "assessment__child__last_name",
        "teacher_summary",
        "evaluator_notes",
    )
    autocomplete_fields = ("assessment",)
    readonly_fields = (
        "created_at",
        "updated_at",
        "deleted_at",
        "final_message",
        "overall_score_display",
        "category_breakdown_display",
        "strengths_display",
        "growth_areas_display",
    )
    fieldsets = (
        ("Assessment", {"fields": ("assessment", "reading_age", "grade_equivalent", "final_message", "overall_score_display")}),
        ("KPI Breakdown", {"fields": ("category_breakdown_display", "strengths_display", "growth_areas_display")}),
        ("Review Notes", {"fields": ("teacher_summary", "evaluator_notes")}),
        ("Raw Data", {"fields": ("final_scores", "category_breakdown", "strengths", "growth_areas", "metadata")}),
        ("Audit", {"fields": ("is_deleted", "deleted_at", "created_at", "updated_at")}),
    )
    actions = ("clear_evaluator_notes",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("assessment", "assessment__child", "assessment__school")

    def child(self, obj):
        return obj.assessment.child

    def school(self, obj):
        return obj.assessment.school

    def reading_age_badge(self, obj):
        return format_html('<strong style="color:#00B8A9;font-size:14px;">{} years</strong>', obj.reading_age)

    reading_age_badge.short_description = "Reading Age"
    reading_age_badge.admin_order_field = "reading_age"

    def final_message(self, obj):
        return obj.final_scores.get("final_message", f"You are reading at an {obj.reading_age}-year-old level")

    def overall_score_display(self, obj):
        score = obj.final_scores.get("overall_score")
        return f"{score}%" if score is not None else "Not scored"

    overall_score_display.short_description = "Overall score"

    def category_breakdown_display(self, obj):
        return pretty_json(obj.category_breakdown)

    category_breakdown_display.short_description = "Full KPI breakdown"

    def strengths_display(self, obj):
        return list_preview(obj.strengths)

    strengths_display.short_description = "Strengths"

    def growth_areas_display(self, obj):
        return list_preview(obj.growth_areas)

    growth_areas_display.short_description = "Growth areas"

    def strengths_preview(self, obj):
        return list_preview(obj.strengths)

    strengths_preview.short_description = "Strengths"

    def growth_areas_preview(self, obj):
        return list_preview(obj.growth_areas)

    growth_areas_preview.short_description = "Growth Areas"

    def has_evaluator_notes(self, obj):
        return bool(obj.evaluator_notes)

    has_evaluator_notes.boolean = True
    has_evaluator_notes.short_description = "Human Notes"

    @admin.action(description="Clear evaluator notes")
    def clear_evaluator_notes(self, request, queryset):
        updated = queryset.update(evaluator_notes="")
        self.message_user(request, f"Cleared evaluator notes for {updated} result(s).", messages.SUCCESS)


def list_preview(values):
    if not values:
        return "None yet"
    return ", ".join(str(value) for value in values[:3])


def pretty_json(value):
    if not value:
        return "No KPI data yet"
    return format_html(
        '<pre style="white-space:pre-wrap;max-width:720px;background:#f8fafc;border:1px solid #e5e7eb;padding:12px;border-radius:6px;">{}</pre>',
        json.dumps(value, indent=2, sort_keys=True),
    )
