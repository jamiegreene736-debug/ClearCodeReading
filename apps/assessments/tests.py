from django.test import SimpleTestCase

from apps.assessments.models import Assessment
from apps.assessments.reading_survey import score_reading_survey
from apps.assessments.views import StatusTransitionSerializer


class AssessmentWorkflowTests(SimpleTestCase):
    def test_status_workflow_choices_exist(self):
        self.assertIn(Assessment.Status.PENDING, Assessment.Status.values)
        self.assertIn(Assessment.Status.HUMAN_REVIEW, Assessment.Status.values)
        self.assertIn(Assessment.Status.COMPLETED, Assessment.Status.values)

    def test_transition_serializer_accepts_human_review(self):
        serializer = StatusTransitionSerializer(data={"status": Assessment.Status.HUMAN_REVIEW})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_transition_serializer_rejects_unknown_status(self):
        serializer = StatusTransitionSerializer(data={"status": "needs_magic"})
        self.assertFalse(serializer.is_valid())

    def test_reading_survey_returns_clear_reading_age(self):
        result = score_reading_survey(
            {
                "phonemicAwareness": 0,
                "letterSound": 0,
                "phonics": 1,
                "advancedPhonics": 1,
                "sightWords": 2,
                "fluency": 1,
                "vocabulary": 0,
                "comprehension": 0,
                "writingReadiness": 1,
                "confidence": 1,
            },
            child_age=7,
        )
        self.assertIn("reading_age", result)
        self.assertIn("Phonics / decoding", result["strengths"])
        self.assertGreater(result["overall_percent"], 60)
