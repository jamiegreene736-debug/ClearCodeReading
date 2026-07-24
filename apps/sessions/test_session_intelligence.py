from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.curriculum.models import Curriculum, CurriculumSequence, StudentPlacement
from apps.schools.models import School, SchoolMembership
from apps.sessions.models import Session, SessionTemplate, SkillObservation
from apps.users.models import ChildProfile, CustomUser


class SessionIntelligenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        original_auto_create_schema = School.auto_create_schema
        School.auto_create_schema = False
        try:
            cls.center = School.objects.create(name="North Center", slug="north", schema_name="north")
            cls.other_center = School.objects.create(name="South Center", slug="south", schema_name="south")
        finally:
            School.auto_create_schema = original_auto_create_schema

        cls.specialist = CustomUser.objects.create_user(
            username="specialist",
            email="specialist@example.com",
            role=CustomUser.Role.TEACHER,
        )
        SchoolMembership.objects.create(
            school=cls.center,
            user=cls.specialist,
            role=SchoolMembership.Role.SPECIALIST,
        )
        cls.child = ChildProfile.objects.create(first_name="Avery", school=cls.center)
        cls.legacy_child = ChildProfile.objects.create(first_name="Morgan", school=cls.center)
        cls.curriculum = Curriculum.objects.create(
            center=cls.center,
            code=Curriculum.Code.PFR,
            name="PFR",
        )
        cls.position = CurriculumSequence.objects.create(
            center=cls.center,
            curriculum=cls.curriculum,
            code="PFR-A-01",
            sequence_order=1,
            level="A",
            lesson_number=1,
            title="Lesson 1",
            position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
        )
        cls.position_without_template = CurriculumSequence.objects.create(
            center=cls.center,
            curriculum=cls.curriculum,
            code="PFR-A-02",
            sequence_order=2,
            level="A",
            lesson_number=2,
            title="Lesson 2",
            position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
        )
        StudentPlacement.objects.create(
            center=cls.center,
            child=cls.child,
            curriculum=cls.curriculum,
            current_position=cls.position,
            methodology_rationale="Instructional placement evidence.",
        )
        StudentPlacement.objects.create(
            center=cls.center,
            child=cls.legacy_child,
            curriculum=cls.curriculum,
            current_position=cls.position_without_template,
            methodology_rationale="Instructional placement evidence.",
        )
        cls.template = SessionTemplate.objects.create(
            center=cls.center,
            curriculum=cls.curriculum,
            curriculum_position=cls.position,
            session_part=Session.InterventionPart.PFR_1A,
            title="PFR Level A Lesson 1 - Session 1a",
            version=1,
            capture_fields={
                "required": [
                    "activities_completed",
                    "item_sets",
                    "time_to_mastery_signals",
                    "next_session_direction",
                    "home_practice_suggestion",
                ],
                "properties": {
                    "activities_completed": {"type": "array", "default": []},
                    "item_sets": {
                        "type": "object",
                        "default": {},
                        "required_keys": ["sound_drill", "word_reading", "word_spelling"],
                    },
                    "time_to_mastery_signals": {"type": "object", "default": {}},
                    "error_patterns": {"type": "array", "default": []},
                    "behavioral_observations": {"type": "array", "default": []},
                },
                "allowed_activity_codes": ["sound_drill", "word_reading", "word_spelling"],
            },
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.specialist)

    @staticmethod
    def _item_set(item_set_id, item_id, *, response_rating=4):
        return {
            "item_set_id": item_set_id,
            "correct": 1,
            "total": 1,
            "items": [
                {
                    "item_id": item_id,
                    "correct": True,
                    "latency_seconds": 2,
                    "mode": "decoding",
                    "prompt_level": "independent",
                    "response_rating": response_rating,
                }
            ],
        }

    def _completed_payload(self):
        now = timezone.now()
        return {
            "child": self.child.id,
            "status": Session.Status.COMPLETED,
            "scheduled_start": now.isoformat(),
            "started_at": now.isoformat(),
            "ended_at": (now + timezone.timedelta(minutes=55)).isoformat(),
            "activities_completed": [
                {
                    "code": "word_reading",
                    "status": "completed",
                    "minutes": 12,
                    "item_set_id": "PFR-A-01-1A-WR-01",
                }
            ],
            "item_sets": {
                "sound_drill": self._item_set("PFR-A-01-1A-SD-01", "sound-1"),
                "word_reading": self._item_set("PFR-A-01-1A-WR-01", "word-1", response_rating=5),
                "word_spelling": self._item_set("PFR-A-01-1A-WS-01", "spelling-1"),
            },
            "accuracy_numerator": 3,
            "accuracy_denominator": 3,
            "time_to_mastery_signals": {
                "cumulative_sessions_at_position": 1,
                "first_attempt_accuracy": 100,
                "latest_accuracy": 100,
                "prompts_per_10_items": 0,
                "independent_transfer": True,
                "reteach": False,
            },
            "error_patterns": [{"code": "short_vowel_confusion", "count": 1, "opportunities": 3}],
            "behavioral_observations": [{"code": "self_correction", "rating": "consistent"}],
            "next_session_direction": "Continue with the distinct Session 1b item set.",
            "home_practice_suggestion": "Read the assigned words once.",
        }

    def test_defaults_load_the_matching_intervention_template(self):
        response = self.client.get("/api/v1/sessions/defaults/", {"child": self.child.id})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["session_template"], self.template.id)
        self.assertEqual(response.data["session_template_version"], 1)
        self.assertEqual(
            response.data["capture_fields"]["properties"]["item_sets"]["required_keys"],
            ["sound_drill", "word_reading", "word_spelling"],
        )
        self.assertEqual(response.data["capture_defaults"]["item_sets"], {})

    def test_template_api_manages_center_scoped_templates(self):
        response = self.client.post(
            "/api/v1/session-templates/",
            {
                "curriculum": self.curriculum.id,
                "curriculum_position": self.position_without_template.id,
                "session_part": Session.InterventionPart.PFR_1A,
                "capture_fields": {
                    "required": ["item_sets"],
                    "properties": {"item_sets": {"type": "object", "default": {}}},
                },
                "title": "PFR Level A Lesson 2 - Session 1a",
                "version": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["center"], self.center.id)
        template = SessionTemplate.objects.get(pk=response.data["id"])

        delete_response = self.client.delete(f"/api/v1/session-templates/{template.id}/")

        self.assertEqual(delete_response.status_code, 204, delete_response.data)
        template.refresh_from_db()
        self.assertTrue(template.is_deleted)
        self.assertEqual(template.updated_by, self.specialist)

    def test_completed_session_creates_queryable_skill_observation(self):
        response = self.client.post("/api/v1/sessions/", self._completed_payload(), format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["session_template"], self.template.id)
        session = Session.objects.get(pk=response.data["id"])
        observation = SkillObservation.objects.get(
            center=self.center,
            child=self.child,
            session=session,
            curriculum_position=self.position,
            is_deleted=False,
        )
        self.assertEqual(observation.accuracy_rate, 100)
        self.assertEqual(observation.response_rating, 4)
        self.assertEqual(observation.error_pattern_tags, ["short_vowel_confusion"])
        self.assertEqual(len(observation.item_references), 3)
        self.assertEqual(observation.time_signals["cumulative_sessions_at_position"], 1)
        self.assertEqual(observation.source_session_revision, session.revision)

        api_response = self.client.get(
            "/api/v1/skill-observations/",
            {"child": self.child.id, "curriculum_position": self.position.id, "session": session.id},
        )
        self.assertEqual(api_response.status_code, 200, api_response.data)
        self.assertEqual([item["id"] for item in api_response.data["results"]], [observation.id])

    def test_template_specific_required_sections_are_validated(self):
        payload = self._completed_payload()
        del payload["item_sets"]["sound_drill"]

        response = self.client.post("/api/v1/sessions/", payload, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Template requires structured sections: sound_drill.", response.data["item_sets"])

    def test_existing_session_creation_without_a_template_still_works(self):
        response = self.client.post(
            "/api/v1/sessions/",
            {
                "child": self.legacy_child.id,
                "status": Session.Status.SCHEDULED,
                "scheduled_start": timezone.now().isoformat(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["curriculum_position"], self.position_without_template.id)
        self.assertIsNone(response.data["session_template"])
        self.assertEqual(response.data["intervention_part"], Session.InterventionPart.PFR_1A)
