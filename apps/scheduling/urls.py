from rest_framework.routers import DefaultRouter

from apps.scheduling.views import (
    GroupViewSet,
    ProviderAvailabilityViewSet,
    ScheduleBookingViewSet,
    ScheduleGroupProposalViewSet,
    WaitlistEntryViewSet,
)

router = DefaultRouter()
router.register("provider-availability", ProviderAvailabilityViewSet, basename="provider-availability")
router.register("schedule-proposals", ScheduleGroupProposalViewSet, basename="schedule-proposal")
router.register("groups", GroupViewSet, basename="group")
router.register("schedule-bookings", ScheduleBookingViewSet, basename="schedule-booking")
router.register("waitlist", WaitlistEntryViewSet, basename="waitlist-entry")

urlpatterns = router.urls
