from rest_framework.routers import DefaultRouter

from .views import FlagViewSet, MilestoneViewSet, OutcomeAggregateViewSet, PredictionViewSet

app_name = "decision_support"

router = DefaultRouter()
router.register("flags", FlagViewSet, basename="flag")
router.register("predictions", PredictionViewSet, basename="prediction")
router.register("milestones", MilestoneViewSet, basename="milestone")
router.register("outcomes", OutcomeAggregateViewSet, basename="outcome")

urlpatterns = router.urls
