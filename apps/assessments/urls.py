from rest_framework.routers import DefaultRouter

from apps.assessments.views import AssessmentViewSet

app_name = "assessments"

router = DefaultRouter()
router.register("assessments", AssessmentViewSet, basename="assessment")

urlpatterns = router.urls
