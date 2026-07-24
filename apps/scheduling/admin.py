from django.contrib import admin

from apps.scheduling.models import ProviderAvailability, ScheduleBooking, WaitlistEntry


@admin.register(ProviderAvailability)
class ProviderAvailabilityAdmin(admin.ModelAdmin):
    list_display = ("specialist", "center", "max_group_size", "is_active", "updated_at")
    list_filter = ("center", "is_active")
    search_fields = ("specialist__email", "specialist__first_name", "specialist__last_name")


@admin.register(ScheduleBooking)
class ScheduleBookingAdmin(admin.ModelAdmin):
    list_display = ("child", "specialist", "center", "starts_at", "status", "sync_status", "scheduler_provider")
    list_filter = ("center", "status", "sync_status", "scheduler_provider", "starts_at")
    search_fields = ("child__first_name", "child__last_name", "specialist__email", "external_booking_id")


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    list_display = ("child", "center", "submarket", "is_active", "created_at")
    list_filter = ("center", "submarket", "is_active", "created_at")
    search_fields = ("child__first_name", "child__last_name", "submarket")
