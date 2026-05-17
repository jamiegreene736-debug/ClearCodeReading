from rest_framework.routers import DefaultRouter

from apps.schools.views import SchoolMembershipViewSet, SchoolViewSet

app_name = "schools"

router = DefaultRouter()
router.register("schools", SchoolViewSet, basename="school")
router.register("memberships", SchoolMembershipViewSet, basename="school-membership")

urlpatterns = router.urls
