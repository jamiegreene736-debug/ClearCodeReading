from rest_framework.routers import DefaultRouter

from apps.outcomes.views import OutcomeSnapshotViewSet

router = DefaultRouter()
router.register("outcomes/snapshots", OutcomeSnapshotViewSet, basename="outcome-snapshot")

urlpatterns = router.urls
