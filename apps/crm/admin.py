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
