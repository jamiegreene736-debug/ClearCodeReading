import uuid
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.curriculum.models import Curriculum, CurriculumSequence, StudentPlacement
from apps.schools.models import School, SchoolMembership
from apps.sessions.models import Session
from apps.users.models import ChildProfile, CustomUser


class RapidSessionLoggingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        auto_schema = School.auto_create_schema
        School.auto_create_schema = False
        try:
            cls.center = School.objects.create(name="Rapid Center", slug="rapid", schema_name="rapid")
        finally:
            School.auto_create_schema = auto_schema
        cls.specialist = CustomUser.objects.create_user(
            username="rapid-specialist",
            email="rapid-specialist@example.com",
            password="test-pass",
            role=CustomUser.Role.TEACHER,
        )
        cls.peer = CustomUser.objects.create_user(
            username="peer-specialist",
            email="peer-specialist@example.com",
            password="test-pass",
            role=CustomUser.Role.TEACHER,
        )
        cls.admin = CustomUser.objects.create_user(
            username="rapid-admin",
            email="rapid-admin@example.com",
            password="test-pass",
            role=CustomUser.Role.SCHOOL_ADMIN,
        )
        cls.viewer = CustomUser.objects.create_user(
            username="rapid-viewer",
            email="rapid-viewer@example.com",
            password="test-pass",
            role=CustomUser.Role.TEACHER,
        )
        for user, role in (
            (cls.specialist, SchoolMembership.Role.SPECIALIST),
            (cls.peer, SchoolMembership.Role.SPECIALIST),
            (cls.admin, SchoolMembership.Role.ADMIN),
            (cls.viewer, SchoolMembership.Role.VIEWER),
        ):
            SchoolMembership.objects.create(school=cls.center, user=user, role=role)
        cls.child = ChildProfile.objects.create(
            first_name="Avery",
            last_name="Reader",
            school=cls.center,
        )
        cls.curriculum = Curriculum.objects.create(
            center=cls.center,
            code=Curriculum.Code.PFR,
            name="Phonics for Reading",
        )
        cls.position = CurriculumSequence.objects.create(
            center=cls.center,
            curriculum=cls.curriculum,
            code="PFR-A-08",
            sequence_order=8,
            level="A",
            lesson_number=8,
            title="Continuous-sound CVC blending",
            position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
            activities=["sound drill", "word reading"],
            item_set_schema={
                "session_1a": ["sound_drill", "word_reading"],
                "session_1b": ["review", "connected_text"],
            },
        )
        StudentPlacement.objects.create(
            center=cls.center,
            child=cls.child,
            curriculum=cls.curriculum,
            current_position=cls.position,
            methodology_rationale="Placement evidence supports this lesson.",
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.specialist)

    def test_defaults_use_active_placement_and_controlled_options(self):
        response = self.api.get(f"/api/v1/sessions/defaults/?child={self.child.id}")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["curriculum_position"], self.position.id)
        self.assertEqual(response.data["intervention_part"], Session.InterventionPart.PFR_1A)
        self.assertEqual(response.data["suggested_activity_codes"], ["sound_drill", "word_reading"])
        self.assertIn("short_vowel_confusion", {item["code"] for item in response.data["error_pattern_options"]})
        self.assertIn("self_correction", {item["code"] for item in response.data["behavioral_observation_options"]})

    def test_minimal_quick_complete_satisfies_clean_and_creates_revision(self):
        response = self.api.post(
            "/api/v1/sessions/rapid-log/",
            {"child": self.child.id, "accuracy_numerator": 9, "accuracy_denominator": 10},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        session = Session.objects.get(pk=response.data["id"])
        session.full_clean()
        self.assertEqual(session.accuracy_rate, 90)
        self.assertTrue(session.item_sets["sound_drill"]["aggregate_only"])
        self.assertEqual(session.revision_history.count(), 1)

    def test_mobile_retry_with_same_request_id_returns_one_session(self):
        request_id = uuid.uuid4()
        payload = {
            "child": self.child.id,
            "client_request_id": str(request_id),
            "accuracy_numerator": 9,
            "accuracy_denominator": 10,
        }

        first = self.api.post("/api/v1/sessions/rapid-log/", payload, format="json")
        second = self.api.post("/api/v1/sessions/rapid-log/", payload, format="json")

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(Session.objects.filter(client_request_id=request_id).count(), 1)

    def test_next_pfr_defaults_to_session_1b(self):
        first = self.api.post(
            "/api/v1/sessions/rapid-log/",
            {"child": self.child.id, "accuracy_numerator": 9, "accuracy_denominator": 10},
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)
        defaults = self.api.get(f"/api/v1/sessions/defaults/?child={self.child.id}")
        self.assertEqual(defaults.data["intervention_part"], Session.InterventionPart.PFR_1B)
        self.assertEqual(defaults.data["suggested_activity_codes"], ["review", "connected_text"])

    def test_full_detail_uses_existing_session_validation(self):
        now = timezone.now()
        response = self.api.post(
            "/api/v1/sessions/rapid-log/",
            {
                "mode": "full_detail",
                "child": self.child.id,
                "full_detail": {
                    "status": "completed",
                    "scheduled_start": now.isoformat(),
                    "started_at": now.isoformat(),
                    "ended_at": (now + timezone.timedelta(minutes=55)).isoformat(),
                    "activities_completed": [{"code": "word_reading", "status": "completed", "minutes": 15, "item_set_id": "PFR-A-08-1A-WR-42"}],
                    "item_sets": {"word_reading": {"item_set_id": "PFR-A-08-1A-WR-42", "correct": 8, "total": 10, "items": [{"item_id": "word-1", "correct": True, "latency_seconds": 3, "mode": "decoding", "prompt_level": "independent"}]}},
                    "accuracy_numerator": 8,
                    "accuracy_denominator": 10,
                    "time_to_mastery_signals": {"cumulative_sessions_at_position": 1, "first_attempt_accuracy": 80, "latest_accuracy": 80, "prompts_per_10_items": 1, "independent_transfer": False, "reteach": False},
                    "error_patterns": [{"code": "short_vowel_confusion", "count": 1, "opportunities": 10}],
                    "behavioral_observations": [{"code": "self_correction", "rating": "consistent"}],
                    "next_session_direction": "Repeat the routine with a fresh item set.",
                    "home_practice_suggestion": "Read three accurate examples once.",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["accuracy_rate"], "80.00")

    def test_only_assigned_specialist_or_center_leadership_can_edit(self):
        scheduled = Session.objects.create(
            center=self.center,
            child=self.child,
            specialist=self.specialist,
            curriculum_position=self.position,
            intervention_part=Session.InterventionPart.PFR_1A,
            scheduled_start=timezone.now(),
            created_by=self.specialist,
            updated_by=self.specialist,
        )
        peer_api = APIClient()
        peer_api.force_authenticate(self.peer)
        denied = peer_api.post(
            "/api/v1/sessions/rapid-log/",
            {"child": self.child.id, "session_id": scheduled.id, "accuracy_numerator": 9, "accuracy_denominator": 10},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)
        admin_api = APIClient()
        admin_api.force_authenticate(self.admin)
        allowed = admin_api.post(
            "/api/v1/sessions/rapid-log/",
            {"child": self.child.id, "session_id": scheduled.id, "accuracy_numerator": 9, "accuracy_denominator": 10},
            format="json",
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)

    def test_viewer_cannot_create(self):
        self.api.force_authenticate(self.viewer)
        response = self.api.post(
            "/api/v1/sessions/rapid-log/",
            {"child": self.child.id, "accuracy_numerator": 9, "accuracy_denominator": 10},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_meaningful_edit_creates_new_revision(self):
        created = self.api.post(
            "/api/v1/sessions/rapid-log/",
            {"child": self.child.id, "accuracy_numerator": 7, "accuracy_denominator": 10},
            format="json",
        )
        updated = self.api.post(
            "/api/v1/sessions/rapid-log/",
            {"child": self.child.id, "session_id": created.data["id"], "accuracy_numerator": 9, "accuracy_denominator": 10},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(Session.objects.get(pk=created.data["id"]).revision_history.count(), 2)

    @patch("apps.decision_support.signals.evaluate_completed_session.apply_async", side_effect=RuntimeError("down"))
    @patch("apps.sessions.signals.send_progress_report_to_parents.apply_async", side_effect=RuntimeError("down"))
    def test_queue_outage_does_not_lose_completed_log(self, _progress, _evaluation):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.api.post(
                "/api/v1/sessions/rapid-log/",
                {"child": self.child.id, "accuracy_numerator": 9, "accuracy_denominator": 10},
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Session.objects.get(pk=response.data["id"]).revision_history.count(), 1)

    def test_server_page_exposes_touch_controls(self):
        from apps.sessions.views import RapidSessionLogView

        request = RequestFactory().get(f"/portal/sessions/rapid-log/?child={self.child.id}")
        request.user = self.specialist
        response = RapidSessionLogView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context_data["defaults"]["curriculum_position"], self.position.id)
