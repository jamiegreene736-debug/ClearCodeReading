from rest_framework.routers import DefaultRouter

from apps.scheduling.views import ProviderAvailabilityViewSet, ScheduleBookingViewSet, WaitlistEntryViewSet

router = DefaultRouter()
router.register("provider-availability", ProviderAvailabilityViewSet, basename="provider-availability")
router.register("schedule-bookings", ScheduleBookingViewSet, basename="schedule-booking")
router.register("waitlist", WaitlistEntryViewSet, basename="waitlist-entry")

urlpatterns = router.urls
