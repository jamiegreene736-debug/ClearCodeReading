from rest_framework.routers import DefaultRouter

from apps.curriculum.views import LessonViewSet, SkillViewSet, TeachingAidViewSet

app_name = "curriculum"

router = DefaultRouter()
router.register("skills", SkillViewSet, basename="skill")
router.register("lessons", LessonViewSet, basename="lesson")
router.register("teaching-aids", TeachingAidViewSet, basename="teaching-aid")

urlpatterns = router.urls
