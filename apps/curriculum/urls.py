from rest_framework.routers import DefaultRouter

from apps.curriculum.views import (
    CurriculumSequenceViewSet,
    CurriculumViewSet,
    LessonViewSet,
    PlacementEvidenceViewSet,
    PlacementRecommendationViewSet,
    SkillViewSet,
    StudentPlacementViewSet,
    TeachingAidViewSet,
)

app_name = "curriculum"

router = DefaultRouter()
router.register("skills", SkillViewSet, basename="skill")
router.register("lessons", LessonViewSet, basename="lesson")
router.register("teaching-aids", TeachingAidViewSet, basename="teaching-aid")
router.register("curricula", CurriculumViewSet, basename="curriculum")
router.register("curriculum-positions", CurriculumSequenceViewSet, basename="curriculum-position")
router.register("placement-evidence", PlacementEvidenceViewSet, basename="placement-evidence")
router.register("placement-recommendations", PlacementRecommendationViewSet, basename="placement-recommendation")
router.register("student-placements", StudentPlacementViewSet, basename="student-placement")

urlpatterns = router.urls
