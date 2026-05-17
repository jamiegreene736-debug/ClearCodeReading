from rest_framework.routers import DefaultRouter

from apps.crm.views import LeadViewSet, OpportunityViewSet

app_name = "crm"

router = DefaultRouter()
router.register("leads", LeadViewSet, basename="lead")
router.register("opportunities", OpportunityViewSet, basename="opportunity")

urlpatterns = router.urls
