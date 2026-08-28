from rest_framework.routers import DefaultRouter

from apps.crm.views import CompanyViewSet, LeadViewSet, OpportunityViewSet

app_name = "crm"

router = DefaultRouter()
router.register("leads", LeadViewSet, basename="lead")
router.register("companies", CompanyViewSet, basename="company")
router.register("deals", OpportunityViewSet, basename="deal")
router.register("opportunities", OpportunityViewSet, basename="opportunity")

urlpatterns = router.urls
