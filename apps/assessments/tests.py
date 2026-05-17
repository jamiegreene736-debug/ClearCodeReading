from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.assessments.models import Assessment
from apps.assessments.reading_survey import score_reading_survey
from apps.assessments.services import calculate_reading_survey_results
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

    def test_scoring_service_calculates_age_message_and_growth_areas(self):
        responses = [
            self._response("phonics", Decimal("1")),
            self._response("phonics", Decimal("1")),
            self._response("comprehension", Decimal("0")),
            self._response("fluency", Decimal("0.5")),
        ]

        result = calculate_reading_survey_results(responses)

        self.assertEqual(result["category_scores"]["phonics"]["score"], 100)
        self.assertEqual(result["category_scores"]["comprehension"]["score"], 0)
        self.assertIn("Phonics / decoding", result["strengths"])
        self.assertIn("Comprehension", result["growth_areas"])
        self.assertEqual(result["final_message"], f"You are reading at an {result['reading_age']:.1f}-year-old level")
        self.assertGreaterEqual(result["reading_age"], 4.0)
        self.assertLessEqual(result["reading_age"], 11.0)

    def _response(self, category, score_value):
        return SimpleNamespace(
            question=SimpleNamespace(
                category=category,
                question_options=[
                    SimpleNamespace(score_value=Decimal("0")),
                    SimpleNamespace(score_value=Decimal("1")),
                ],
            ),
            selected_option=None,
            selected_option_id=None,
            score_value=score_value,
            is_correct=None,
        )
