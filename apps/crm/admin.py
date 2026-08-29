from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.crm.models import (
    Company,
    CrmActivity,
    FormSubmission,
    IntakeTriage,
    Lead,
    NewsletterCampaign,
    NewsletterDelivery,
    NewsletterSubscription,
    Opportunity,
)
from apps.crm.newsletters import (
    NewsletterSendError,
    newsletter_delivery_configuration_errors,
    send_newsletter_campaign,
)


class OpportunityInline(admin.TabularInline):
    model = Opportunity
    fk_name = "lead"
    extra = 0
    autocomplete_fields = ("company", "school", "owner")
    fields = ("name", "pipeline", "stage", "company", "priority", "value", "probability", "expected_close_date", "owner")


class CompanyOpportunityInline(OpportunityInline):
    fk_name = "company"


class ContactInline(admin.TabularInline):
    model = Lead
    extra = 0
    fields = ("contact_name", "contact_email", "status", "assigned_to")
    readonly_fields = ("contact_email",)
    autocomplete_fields = ("assigned_to",)


class FormSubmissionInline(admin.TabularInline):
    model = FormSubmission
    extra = 0
    fields = ("form_type", "source_path", "submitted_data", "created_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class CrmActivityInline(admin.TabularInline):
    model = CrmActivity
    extra = 0
    fields = ("activity_type", "subject", "due_at", "completed_at", "created_by", "assigned_to")
    readonly_fields = ("created_at",)


@admin.action(description="Mark selected leads as contacted")
def mark_contacted(modeladmin, request, queryset):
    queryset.update(status=Lead.Status.CONTACTED, updated_at=timezone.now())


@admin.action(description="Mark selected leads as qualified")
def mark_qualified(modeladmin, request, queryset):
    queryset.update(status=Lead.Status.QUALIFIED, updated_at=timezone.now())


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    inlines = (FormSubmissionInline, CrmActivityInline, OpportunityInline)
    list_display = (
        "school_name",
        "contact_name",
        "contact_email",
        "company",
        "audience",
        "source",
        "status",
        "linked_user",
        "assigned_to",
        "estimated_students",
        "created_at",
    )
    list_filter = ("status", "audience", "source", "company", "assigned_to", "linked_user", "is_deleted", "created_at")
    search_fields = ("school_name", "organization_name", "contact_name", "contact_email", "contact_phone", "notes")
    autocomplete_fields = ("company", "assigned_to", "linked_user")
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = (mark_contacted, mark_qualified)

    def has_add_permission(self, request):
        return False


@admin.register(FormSubmission)
class FormSubmissionAdmin(admin.ModelAdmin):
    list_display = ("form_type", "lead", "source_path", "created_at")
    list_filter = ("form_type", "source_path", "created_at")
    search_fields = ("lead__contact_name", "lead__contact_email", "submitted_data")
    readonly_fields = ("lead", "form_type", "source_path", "submitted_data", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CrmActivity)
class CrmActivityAdmin(admin.ModelAdmin):
    list_display = ("activity_type", "lead", "subject", "assigned_to", "due_at", "completed_at", "created_at")
    list_filter = ("activity_type", "assigned_to", "completed_at", "due_at")
    search_fields = ("lead__contact_name", "lead__contact_email", "subject", "body")
    autocomplete_fields = ("lead", "created_by", "assigned_to")
    readonly_fields = ("created_at", "updated_at")


@admin.action(description="Close selected deals unsuccessfully")
def close_unsuccessful(modeladmin, request, queryset):
    terminal_stage_by_pipeline = {
        Opportunity.Pipeline.FAMILY_ENROLLMENT: Opportunity.Stage.FAMILY_LOST,
        Opportunity.Pipeline.REFERRAL_PARTNERS: Opportunity.Stage.PARTNER_DORMANT,
        Opportunity.Pipeline.FOUNDATION_DONORS: Opportunity.Stage.DONOR_DECLINED,
        Opportunity.Pipeline.FOUNDATION_GRANTS: Opportunity.Stage.GRANT_DECLINED,
        Opportunity.Pipeline.EQUITY_INVESTMENT: Opportunity.Stage.EQUITY_PASSED,
    }
    now = timezone.now()
    for pipeline, terminal_stage in terminal_stage_by_pipeline.items():
        queryset.filter(pipeline=pipeline).update(stage=terminal_stage, closed_at=now, updated_at=now)


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = ("name", "deal_label", "pipeline", "stage", "priority", "company", "lead", "owner", "value", "expected_close_date", "closed_at")
    list_filter = ("pipeline", "stage", "priority", "owner", "company", "expected_close_date", "closed_at", "is_deleted", "created_at")
    search_fields = ("name", "student_name", "program_name", "investment_round", "segment_tags", "company__name", "lead__school_name", "lead__contact_name", "school__name", "owner__email", "next_steps", "lost_reason")
    autocomplete_fields = ("lead", "company", "referral_partner", "school", "owner", "related_deals")
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = (close_unsuccessful,)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    inlines = (ContactInline, CompanyOpportunityInline)
    list_display = ("name", "owner", "website", "created_at")
    list_filter = ("owner", "is_deleted", "created_at")
    search_fields = ("name", "website", "notes")
    autocomplete_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at", "deleted_at")


@admin.register(IntakeTriage)
class IntakeTriageAdmin(admin.ModelAdmin):
    list_display = ("source_signal", "lead", "status", "resolved_by", "resolved_at", "created_at")
    list_filter = ("source_signal", "status", "resolved_by", "created_at")
    search_fields = ("lead__contact_name", "lead__contact_email", "resolution_notes")
    autocomplete_fields = ("lead", "resolved_by", "created_deals")
    readonly_fields = ("submission", "source_signal", "created_at", "updated_at", "resolved_at", "created_deals")


@admin.action(description="Unsubscribe selected email addresses")
def unsubscribe_selected(modeladmin, request, queryset):
    queryset.filter(status=NewsletterSubscription.Status.ACTIVE).update(
        status=NewsletterSubscription.Status.UNSUBSCRIBED,
        unsubscribed_at=timezone.now(),
        updated_at=timezone.now(),
    )


@admin.action(description="Reactivate selected email addresses with confirmed consent")
def reactivate_selected(modeladmin, request, queryset):
    queryset.update(
        status=NewsletterSubscription.Status.ACTIVE,
        consented_at=timezone.now(),
        unsubscribed_at=None,
        updated_at=timezone.now(),
    )


@admin.register(NewsletterSubscription)
class NewsletterSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("email", "name", "status", "consented_at", "unsubscribed_at", "last_sent_at")
    list_filter = ("status", "consent_version", "consented_at", "unsubscribed_at")
    search_fields = ("email", "name")
    readonly_fields = ("created_at", "updated_at", "last_sent_at")
    actions = (unsubscribe_selected, reactivate_selected)


class NewsletterDeliveryInline(admin.TabularInline):
    model = NewsletterDelivery
    extra = 0
    can_delete = False
    fields = ("recipient_email", "status", "attempts", "sent_at", "last_error")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(NewsletterCampaign)
class NewsletterCampaignAdmin(admin.ModelAdmin):
    change_form_template = "admin/crm/newslettercampaign/change_form.html"
    list_display = (
        "subject",
        "status",
        "recipient_count",
        "delivered_count",
        "failed_count",
        "sent_at",
        "created_by",
        "send_link",
    )
    list_filter = ("status", "created_at", "sent_at")
    search_fields = ("subject", "preview_text", "body")
    readonly_fields = (
        "created_at",
        "updated_at",
        "created_by",
        "sent_by",
        "sending_started_at",
        "sent_at",
        "recipient_count",
        "delivered_count",
        "failed_count",
    )
    inlines = (NewsletterDeliveryInline,)

    def get_urls(self):
        return [
            path(
                "<int:campaign_id>/send/",
                self.admin_site.admin_view(self.send_view),
                name="crm_newslettercampaign_send",
            ),
        ] + super().get_urls()

    def save_model(self, request, obj, form, change):
        if obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.status != NewsletterCampaign.Status.DRAFT:
            readonly_fields.extend(["subject", "preview_text", "body"])
        return readonly_fields

    @admin.display(description="Send")
    def send_link(self, obj):
        if obj.status == NewsletterCampaign.Status.SENT:
            return "Complete"
        label = "Retry failed" if obj.status == NewsletterCampaign.Status.PARTIALLY_FAILED else "Review & send"
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:crm_newslettercampaign_send", args=[obj.pk]),
            label,
        )

    def send_view(self, request, campaign_id):
        campaign = get_object_or_404(NewsletterCampaign, pk=campaign_id)
        if not self.has_change_permission(request, campaign):
            raise PermissionDenied

        if request.method == "POST":
            try:
                campaign = send_newsletter_campaign(campaign.pk, sent_by=request.user)
            except NewsletterSendError as error:
                self.message_user(request, str(error), level=messages.ERROR)
            else:
                if campaign.failed_count:
                    self.message_user(
                        request,
                        f"Newsletter sent to {campaign.delivered_count} recipients; "
                        f"{campaign.failed_count} deliveries can be retried.",
                        level=messages.WARNING,
                    )
                else:
                    self.message_user(
                        request,
                        f"Newsletter sent to {campaign.delivered_count} recipients.",
                        level=messages.SUCCESS,
                    )
            return redirect("admin:crm_newslettercampaign_change", campaign.pk)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": campaign,
            "title": "Review and send newsletter",
            "active_subscriber_count": NewsletterSubscription.objects.filter(
                status=NewsletterSubscription.Status.ACTIVE,
            ).count(),
            "retry_count": campaign.deliveries.filter(status=NewsletterDelivery.Status.FAILED).count(),
            "configuration_errors": newsletter_delivery_configuration_errors(),
        }
        return TemplateResponse(request, "admin/crm/newslettercampaign/send_confirmation.html", context)


@admin.register(NewsletterDelivery)
class NewsletterDeliveryAdmin(admin.ModelAdmin):
    list_display = ("campaign", "recipient_email", "status", "attempts", "sent_at")
    list_filter = ("status", "campaign", "sent_at")
    search_fields = ("recipient_email", "campaign__subject", "last_error")
    readonly_fields = (
        "campaign",
        "subscription",
        "recipient_email",
        "status",
        "attempts",
        "sent_at",
        "last_error",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
