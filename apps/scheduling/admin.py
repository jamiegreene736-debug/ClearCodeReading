from django.contrib import admin

from apps.scheduling.integrations import SchedulerNotConfigured, get_scheduler_adapter
from apps.scheduling.models import ProviderAvailability, ScheduleBooking, ScheduleGroupProposal, WaitlistEntry
from apps.scheduling.optimizer import ProposalConflict, approve_group_proposal
from apps.scheduling.services import sync_booking


class CenterScopedAdminMixin:
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        center_ids = request.user.school_memberships.filter(is_deleted=False).values_list("school_id", flat=True)
        return queryset.filter(center_id__in=center_ids)


@admin.register(ProviderAvailability)
class ProviderAvailabilityAdmin(CenterScopedAdminMixin, admin.ModelAdmin):
    list_display = ("specialist", "center", "max_group_size", "is_active", "updated_at")
    list_filter = ("center", "is_active")
    search_fields = ("specialist__email", "specialist__first_name", "specialist__last_name")


@admin.register(ScheduleBooking)
class ScheduleBookingAdmin(CenterScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "child",
        "iep_consent",
        "specialist",
        "center",
        "starts_at",
        "status",
        "sync_status",
        "scheduler_provider",
    )
    list_filter = ("center", "status", "sync_status", "scheduler_provider", "starts_at")
    search_fields = ("child__first_name", "child__last_name", "specialist__email", "external_booking_id")
    actions = ["resync_approved_bookings"]

    @admin.display(boolean=True, description="IEP consent ready")
    def iep_consent(self, booking):
        return booking.child.idea_services_authorized

    @admin.action(description="Re-sync selected approved/confirmed bookings")
    def resync_approved_bookings(self, request, queryset):
        try:
            adapter = get_scheduler_adapter()
        except SchedulerNotConfigured as error:
            self.message_user(request, str(error), level="error")
            return
        synced = 0
        errors = 0
        for booking in queryset.select_related("child", "specialist", "center"):
            if booking.status not in [ScheduleBooking.Status.APPROVED, ScheduleBooking.Status.CONFIRMED]:
                continue
            booking = sync_booking(booking, adapter)
            if booking.sync_status == ScheduleBooking.SyncStatus.SYNCED:
                synced += 1
            else:
                errors += 1
        self.message_user(request, f"Synced {synced} bookings; {errors} require operations review.")


@admin.register(ScheduleGroupProposal)
class ScheduleGroupProposalAdmin(CenterScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "center",
        "specialist",
        "curriculum",
        "starts_at",
        "group_size",
        "score",
        "status",
        "pending_iep_consent",
    )
    list_filter = ("center", "status", "curriculum__code", "starts_at")
    search_fields = ("specialist__email", "children__first_name", "children__last_name")
    filter_horizontal = ("children",)
    actions = ["bulk_approve"]

    @admin.display(description="Students")
    def group_size(self, proposal):
        return proposal.children.count()

    @admin.display(boolean=True, description="Pending IEP consent")
    def pending_iep_consent(self, proposal):
        return any(not child.idea_services_authorized for child in proposal.children.all())

    @admin.action(description="Approve selected advisory proposals")
    def bulk_approve(self, request, queryset):
        approved = 0
        blocked = 0
        for proposal in queryset:
            try:
                approve_group_proposal(proposal, request.user)
                approved += 1
            except ProposalConflict:
                blocked += 1
        self.message_user(request, f"Approved {approved} proposals; {blocked} were blocked for review.")


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(CenterScopedAdminMixin, admin.ModelAdmin):
    list_display = ("child", "center", "submarket", "is_active", "created_at")
    list_filter = ("center", "submarket", "is_active", "created_at")
    search_fields = ("child__first_name", "child__last_name", "submarket")
