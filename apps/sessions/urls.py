from rest_framework.routers import DefaultRouter

from apps.sessions.views import SessionTemplateViewSet, SessionViewSet, SkillObservationViewSet


app_name = "intervention_sessions"

router = DefaultRouter()
router.register("sessions", SessionViewSet, basename="intervention-session")
router.register("session-templates", SessionTemplateViewSet, basename="session-template")
router.register("skill-observations", SkillObservationViewSet, basename="skill-observation")

urlpatterns = router.urls
