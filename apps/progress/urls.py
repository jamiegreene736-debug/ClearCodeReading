from rest_framework.routers import DefaultRouter

from apps.progress.views import MasteryRecordViewSet, ProgressViewSet

app_name = "progress"

router = DefaultRouter()
router.register("progress", ProgressViewSet, basename="progress")
router.register("mastery-records", MasteryRecordViewSet, basename="mastery-record")

urlpatterns = router.urls
