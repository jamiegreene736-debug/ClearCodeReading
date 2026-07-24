from rest_framework.routers import DefaultRouter

from apps.decision_support.views import GrowthFlagViewSet, MilestonePredictionViewSet


app_name = "decision_support"

router = DefaultRouter()
router.register("growth-flags", GrowthFlagViewSet, basename="growth-flag")
router.register("milestone-predictions", MilestonePredictionViewSet, basename="milestone-prediction")

urlpatterns = router.urls
