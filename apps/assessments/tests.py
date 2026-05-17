from django.test import SimpleTestCase

from apps.assessments.models import Assessment
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
