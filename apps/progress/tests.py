from django.test import SimpleTestCase

from apps.progress.models import Progress
from apps.progress.serializers import MasteryRecordSerializer, ProgressSerializer


class ProgressTests(SimpleTestCase):
    def test_progress_statuses_include_mastered(self):
        self.assertIn(Progress.Status.MASTERED, Progress.Status.values)

    def test_progress_serializer_has_dashboard_supporting_fields(self):
        fields = ProgressSerializer().fields
        self.assertIn("current_score", fields)
        self.assertIn("last_assessment_detail", fields)

    def test_mastery_serializer_exposes_mastered_at(self):
        self.assertIn("mastered_at", MasteryRecordSerializer().fields)
