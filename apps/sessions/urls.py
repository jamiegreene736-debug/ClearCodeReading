from rest_framework.routers import DefaultRouter

from apps.sessions.views import SessionViewSet


app_name = "intervention_sessions"

router = DefaultRouter()
router.register("sessions", SessionViewSet, basename="intervention-session")

urlpatterns = router.urls
