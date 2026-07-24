from rest_framework.routers import DefaultRouter

from apps.users.views import (
    AuditLogViewSet,
    ChildProfileViewSet,
    ConsentLogViewSet,
    ConsentRecordViewSet,
    GuardianRelationshipViewSet,
    ProfileViewSet,
    UserViewSet,
)

app_name = "users"

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("profiles", ProfileViewSet, basename="profile")
router.register("children", ChildProfileViewSet, basename="child")
router.register("guardian-relationships", GuardianRelationshipViewSet, basename="guardian-relationship")
router.register("consents", ConsentLogViewSet, basename="consent")
router.register("consent-records", ConsentRecordViewSet, basename="consent-record")
router.register("audit-logs", AuditLogViewSet, basename="audit-log")

urlpatterns = router.urls
