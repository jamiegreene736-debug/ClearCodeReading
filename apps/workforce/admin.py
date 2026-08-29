from django.contrib import admin

from apps.workforce.models import (
    Agreement,
    ClassificationReview,
    ComplianceTask,
    Credential,
    Engagement,
    PayableItem,
    Payment,
    PaymentRun,
    PayerLegalEntity,
    ProviderEvent,
    ProviderOnboarding,
    RateSchedule,
    SensitiveDataReference,
    TaxYearSummary,
    WorkerAssignment,
    WorkerProfile,
    WorkforceRoleMembership,
)


@admin.register(PayerLegalEntity)
class PayerLegalEntityAdmin(admin.ModelAdmin):
    list_display = ["legal_name", "jurisdiction_state", "is_active"]


@admin.register(WorkforceRoleMembership)
class WorkforceRoleMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "payer", "role", "is_active"]
    list_filter = ["payer", "role", "is_active"]


@admin.register(WorkerProfile)
class WorkerProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "status", "created_at"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]


@admin.register(Engagement)
class EngagementAdmin(admin.ModelAdmin):
    list_display = ["worker", "payer", "classification", "status", "work_state", "starts_on"]
    list_filter = ["payer", "classification", "status", "work_state", "delivery_context"]
    search_fields = ["worker__user__email", "worker__user__first_name", "worker__user__last_name"]
    readonly_fields = ["classification", "status"]


@admin.register(ClassificationReview)
class ClassificationReviewAdmin(admin.ModelAdmin):
    list_display = ["engagement", "version", "decision", "reviewed_by", "reviewed_at", "next_review_due"]
    readonly_fields = ["engagement", "version", "decision", "rationale", "evidence", "reviewed_by", "reviewed_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProviderOnboarding)
class ProviderOnboardingAdmin(admin.ModelAdmin):
    list_display = ["engagement", "provider", "status", "last_synced_at"]
    exclude = ["external_onboarding_id"]


@admin.register(SensitiveDataReference)
class SensitiveDataReferenceAdmin(admin.ModelAdmin):
    list_display = ["engagement", "provider", "custodian", "status", "verified_at"]
    exclude = ["external_subject_id"]


@admin.register(RateSchedule)
class RateScheduleAdmin(admin.ModelAdmin):
    list_display = ["engagement", "center", "unit", "amount", "status", "starts_on"]
    readonly_fields = ["status", "approved_by", "approved_at"]


@admin.register(PayableItem)
class PayableItemAdmin(admin.ModelAdmin):
    list_display = ["engagement", "center", "service_date", "gross_amount", "status"]
    list_filter = ["center", "status"]
    readonly_fields = ["engagement", "center", "source_session", "service_date", "units", "rate", "gross_amount", "status", "created_by", "approved_by", "approved_at"]

    def has_add_permission(self, request):
        return False


@admin.register(PaymentRun)
class PaymentRunAdmin(admin.ModelAdmin):
    list_display = ["payer", "period_start", "period_end", "status", "created_by", "total_amount"]
    readonly_fields = ["idempotency_key", "status", "reviewed_by", "reviewed_at", "approved_by", "approved_at", "external_batch_id"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["payment_run", "engagement", "amount", "status"]
    readonly_fields = [field.name for field in Payment._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(ProviderEvent)
class ProviderEventAdmin(admin.ModelAdmin):
    list_display = ["provider", "external_event_id", "event_type", "status", "processed_at"]
    readonly_fields = [field.name for field in ProviderEvent._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(Agreement)
class AgreementAdmin(admin.ModelAdmin):
    list_display = [
        "worker_name",
        "agreement_type",
        "status",
        "effective_on",
        "expires_on",
    ]
    list_filter = ["kind", "status"]
    search_fields = [
        "engagement__worker__user__email",
        "engagement__worker__user__first_name",
        "engagement__worker__user__last_name",
    ]
    list_select_related = ["engagement__worker__user"]
    ordering = ["-effective_on", "-created_at"]

    @admin.display(
        description="Worker",
        ordering="engagement__worker__user__last_name",
    )
    def worker_name(self, obj: Agreement) -> str:
        return str(obj.engagement.worker)

    @admin.display(description="Agreement type", ordering="kind")
    def agreement_type(self, obj: Agreement) -> str:
        return obj.get_kind_display()


admin.site.register(WorkerAssignment)
admin.site.register(Credential)
admin.site.register(ComplianceTask)
admin.site.register(TaxYearSummary)
