from django.test import RequestFactory, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.curriculum.models import Curriculum, CurriculumSequence, StudentPlacement
from apps.progress.models import Progress
from apps.curriculum.models import Skill
from apps.schools.models import School
from apps.sessions.models import Session
from apps.users.models import ChildProfile, ConsentLog, CustomUser, GuardianRelationship
from apps.users.portal_views import PortalDashboardView


class ParentDashboardIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        original = School.auto_create_schema
        School.auto_create_schema = False
        try:
            cls.center = School.objects.create(name="Launch Center", slug="launch", schema_name="launch")
        finally:
            School.auto_create_schema = original
        cls.parent = CustomUser.objects.create_user(username="parent-phase2", email="parent-phase2@example.com", role=CustomUser.Role.GUARDIAN)
        cls.other_parent = CustomUser.objects.create_user(username="other-parent", email="other-parent@example.com", role=CustomUser.Role.GUARDIAN)
        cls.specialist = CustomUser.objects.create_user(username="specialist-phase2", email="specialist-phase2@example.com", role=CustomUser.Role.TEACHER)
        cls.child = ChildProfile.objects.create(first_name="Avery", school=cls.center)
        cls.relationship = GuardianRelationship.objects.create(
            guardian=cls.parent,
            child=cls.child,
            relationship_type=GuardianRelationship.RelationshipType.PARENT,
            consent_status=GuardianRelationship.ConsentStatus.GRANTED,
            permissions={"progress_dashboard": True},
        )
        for consent_type in ["assessment", "data_processing", "school_sharing"]:
            ConsentLog.objects.create(
                guardian_relationship=cls.relationship,
                guardian=cls.parent,
                child=cls.child,
                consent_type=consent_type,
                status=ConsentLog.Status.GRANTED,
            )
        curriculum = Curriculum.objects.create(center=cls.center, code=Curriculum.Code.PFR, name="PFR")
        position = CurriculumSequence.objects.create(
            center=cls.center,
            curriculum=curriculum,
            code="PFR-A-01",
            sequence_order=1,
            level="A",
            lesson_number=1,
            title="Lesson 1",
            position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
        )
        StudentPlacement.objects.create(
            center=cls.center,
            child=cls.child,
            curriculum=curriculum,
            current_position=position,
            methodology_rationale="Placement evidence.",
        )
        skill = Skill.objects.create(code="PH-CVC", name="Short-vowel words", domain=Skill.Domain.PHONICS)
        Progress.objects.create(child=cls.child, school=cls.center, skill=skill, status=Progress.Status.DEVELOPING, current_score=82)
        now = timezone.now()
        session = Session.objects.create(
            center=cls.center,
            child=cls.child,
            specialist=cls.specialist,
            curriculum_position=position,
            status=Session.Status.COMPLETED,
            intervention_part=Session.InterventionPart.PFR_1A,
            scheduled_start=now,
            started_at=now,
            ended_at=now + timezone.timedelta(minutes=45),
            activities_completed=[{"code": "decodable_reading", "status": "completed", "minutes": 10, "item_set_id": "d-1"}],
            item_sets={"decodable_text": {"item_set_id": "d-1", "type": "decodable_text", "title": "Sam Sits", "wcpm": 34, "items": []}},
            accuracy_rate=90,
            accuracy_numerator=9,
            accuracy_denominator=10,
            time_to_mastery_signals={"wcpm": 34},
            error_patterns=[],
            next_session_direction="Avery is reading short-vowel words with growing confidence.",
            home_practice_suggestion="Read Sam Sits once each evening.",
        )
        session.targeted_positions.add(position)

    def setUp(self):
        self.client = APIClient()

    def test_active_guardian_sees_live_session_dashboard(self):
        self.client.force_authenticate(self.parent)
        response = self.client.get(f"/api/v1/progress/dashboard/?child={self.child.id}")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["latest_accuracy"], 90.0)
        self.assertEqual(response.data["fluency_trend"][0]["wcpm"], 34)
        self.assertEqual(response.data["decodable_text_progress"][0]["title"], "Sam Sits")
        self.assertIn("growing confidence", response.data["specialist_note"])
        self.assertEqual(response.data["home_practice"], "Read Sam Sits once each evening.")

    def test_unrelated_guardian_cannot_view_dashboard(self):
        self.client.force_authenticate(self.other_parent)
        response = self.client.get(f"/api/v1/progress/dashboard/?child={self.child.id}")
        self.assertEqual(response.status_code, 403)

    def test_guardian_permission_can_hide_dashboard(self):
        self.relationship.permissions = {"progress_dashboard": False}
        self.relationship.save(update_fields=["permissions", "updated_at"])
        self.client.force_authenticate(self.parent)
        response = self.client.get(f"/api/v1/progress/dashboard/?child={self.child.id}")
        self.assertEqual(response.status_code, 403)

    def test_parent_portal_renders_live_dashboard_mobile_sections(self):
        request = RequestFactory().get("/dashboard/")
        request.user = self.parent
        request.session = {}
        response = PortalDashboardView.as_view()(request)
        response.render()
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("This week's reading growth", content)
        self.assertIn("Fluency (WCPM)", content)
        self.assertIn("Read Sam Sits once each evening.", content)
