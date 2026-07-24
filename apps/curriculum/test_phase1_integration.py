from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.curriculum.models import (
    Curriculum,
    CurriculumSequence,
    PlacementEvidence,
    PlacementRecommendation,
    RecommendedSequencePosition,
    SequencePlan,
    SequencePlanItem,
    SkillCrosswalk,
    StudentPlacement,
)
from apps.schools.models import School, SchoolMembership
from apps.users.models import ChildProfile, CustomUser


class PhaseOneWorkflowIntegrationTests(TestCase):
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
        cls.other_child = ChildProfile.objects.create(first_name="Jordan", school=cls.other_center)
        cls.curriculum = Curriculum.objects.create(
            center=cls.center,
            code=Curriculum.Code.PFR,
            name="PFR",
        )
        cls.other_curriculum = Curriculum.objects.create(
            center=cls.other_center,
            code=Curriculum.Code.PFR,
            name="PFR",
        )
        cls.position_one = CurriculumSequence.objects.create(
            center=cls.center,
            curriculum=cls.curriculum,
            code="PFR-A-01",
            sequence_order=1,
            level="A",
            lesson_number=1,
            title="Lesson 1",
            position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
        )
        cls.position_two = CurriculumSequence.objects.create(
            center=cls.center,
            curriculum=cls.curriculum,
            code="PFR-A-02",
            sequence_order=2,
            level="A",
            lesson_number=2,
            title="Lesson 2",
            position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
        )
        cls.other_position = CurriculumSequence.objects.create(
            center=cls.other_center,
            curriculum=cls.other_curriculum,
            code="PFR-A-01",
            sequence_order=1,
            level="A",
            lesson_number=1,
            title="Other Lesson",
            position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.specialist)

    def _evidence(self, child=None, curriculum=None, center=None):
        return PlacementEvidence.objects.create(
            center=center or self.center,
            child=child or self.child,
            curriculum=curriculum or self.curriculum,
            instrument=PlacementEvidence.Instrument.PFR_PLACEMENT,
            assessment_version="2026.1",
            administered_by=self.specialist,
            raw_results={"parts": [{"position_code": "PFR-A-01", "items": [{"correct": True}]}]},
        )

    def test_placement_evidence_api_isolates_centers(self):
        visible = self._evidence()
        self._evidence(
            child=self.other_child,
            curriculum=self.other_curriculum,
            center=self.other_center,
        )

        response = self.client.get("/api/v1/placement-evidence/")

        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.data["results"]}
        self.assertEqual(ids, {visible.id})

    def test_completed_evidence_generates_ranked_explainable_recommendation(self):
        evidence = self._evidence()

        response = self.client.post(f"/api/v1/placement-evidence/{evidence.id}/recommend/", {}, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["recommended_position_detail"]["code"], "PFR-A-02")
        self.assertEqual(response.data["recommended_sequence"][0]["priority"], 1)
        self.assertEqual(response.data["rule_trace"]["basal"], "PFR-A-01")
        self.assertEqual(response.data["ai_metadata"]["provider"], "disabled")

    def test_override_is_persisted_as_labeled_training_signal(self):
        evidence = self._evidence()
        recommendation = PlacementRecommendation.objects.create(
            center=self.center,
            evidence=evidence,
            recommended_curriculum=self.curriculum,
            recommended_position=self.position_one,
            decision=PlacementRecommendation.Decision.PLACE,
            rationale="Deterministic rule result.",
            rule_trace={"rule": "test"},
        )
        placement = StudentPlacement.objects.create(
            center=self.center,
            child=self.child,
            curriculum=self.curriculum,
            current_position=self.position_one,
            methodology_rationale="Initial placement.",
        )

        response = self.client.post(
            f"/api/v1/placement-recommendations/{recommendation.id}/confirm/",
            {
                "final_position": self.position_two.id,
                "override_rationale": "Recent item-level evidence supports Lesson 2.",
                "evidence_considered": {"item_set_ids": ["review-17"]},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        recommendation.refresh_from_db()
        placement.refresh_from_db()
        override = placement.override_history.get()
        self.assertEqual(recommendation.status, PlacementRecommendation.Status.OVERRIDDEN)
        self.assertEqual(placement.current_position, self.position_two)
        self.assertEqual(override.source_recommendation, recommendation)
        self.assertEqual(override.evidence_considered["item_set_ids"], ["review-17"])

    def test_confirmation_materializes_ranked_working_plan(self):
        evidence = self._evidence()
        recommendation = PlacementRecommendation.objects.create(
            center=self.center,
            evidence=evidence,
            recommended_curriculum=self.curriculum,
            recommended_position=self.position_one,
            decision=PlacementRecommendation.Decision.PLACE,
            rationale="Deterministic rule result.",
            rule_trace={"rule": "test"},
        )
        for priority, position in enumerate([self.position_one, self.position_two], start=1):
            RecommendedSequencePosition.objects.create(
                recommendation=recommendation,
                position=position,
                priority=priority,
            )

        response = self.client.post(
            f"/api/v1/placement-recommendations/{recommendation.id}/confirm/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        plan = SequencePlan.objects.get(created_from_recommendation=recommendation)
        self.assertEqual(plan.status, SequencePlan.Status.ACTIVE)
        self.assertEqual(
            list(plan.items.values_list("position_id", "status")),
            [
                (self.position_one.id, SequencePlanItem.Status.IN_PROGRESS),
                (self.position_two.id, SequencePlanItem.Status.PENDING),
            ],
        )
        self.assertEqual(response.data["materialized_sequence_plan"]["id"], plan.id)

        item = plan.items.get(position=self.position_one)
        update_response = self.client.patch(
            f"/api/v1/sequence-plans/{plan.id}/items/{item.id}/",
            {"status": SequencePlanItem.Status.MASTERED, "notes": "Demonstrated independently."},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200, update_response.data)
        item.refresh_from_db()
        self.assertEqual(item.status, SequencePlanItem.Status.MASTERED)

    def test_confirmation_can_decline_plan_materialization(self):
        evidence = self._evidence()
        recommendation = PlacementRecommendation.objects.create(
            center=self.center,
            evidence=evidence,
            recommended_curriculum=self.curriculum,
            recommended_position=self.position_one,
            decision=PlacementRecommendation.Decision.PLACE,
            rationale="Deterministic rule result.",
            rule_trace={"rule": "test"},
        )

        response = self.client.post(
            f"/api/v1/placement-recommendations/{recommendation.id}/confirm/",
            {"create_sequence_plan": False},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(SequencePlan.objects.filter(created_from_recommendation=recommendation).exists())

    def test_crosswalk_rejects_cross_center_nodes(self):
        crosswalk = SkillCrosswalk(
            center=self.center,
            skill_node_a=self.position_one,
            skill_node_b=self.other_position,
            mapping_type=SkillCrosswalk.MappingType.OVERLAPS,
            equivalence="0.750",
            version="2026.1",
        )

        with self.assertRaises(ValidationError):
            crosswalk.full_clean()

    def test_completed_structured_session_can_be_logged_through_api(self):
        StudentPlacement.objects.create(
            center=self.center,
            child=self.child,
            curriculum=self.curriculum,
            current_position=self.position_one,
            methodology_rationale="Placement evidence.",
        )
        now = timezone.now()
        response = self.client.post(
            "/api/v1/sessions/",
            {
                "child": self.child.id,
                "status": "completed",
                "scheduled_start": now.isoformat(),
                "started_at": now.isoformat(),
                "ended_at": (now + timezone.timedelta(minutes=55)).isoformat(),
                "activities_completed": [
                    {
                        "code": "word_reading",
                        "status": "completed",
                        "minutes": 12,
                        "item_set_id": "PFR-A-01-1A-WR-02",
                    }
                ],
                "item_sets": {
                    "word_reading": {
                        "item_set_id": "PFR-A-01-1A-WR-02",
                        "correct": 9,
                        "total": 10,
                        "items": [
                            {
                                "item_id": "1",
                                "correct": True,
                                "latency_seconds": 3,
                                "mode": "decoding",
                                "prompt_level": "independent",
                            }
                        ],
                    }
                },
                "accuracy_numerator": 9,
                "accuracy_denominator": 10,
                "time_to_mastery_signals": {
                    "cumulative_sessions_at_position": 1,
                    "first_attempt_accuracy": 90,
                    "latest_accuracy": 90,
                    "prompts_per_10_items": 1,
                    "independent_transfer": False,
                    "reteach": False,
                },
                "error_patterns": [{"code": "short_vowel_confusion", "count": 1, "opportunities": 10}],
                "behavioral_observations": [{"code": "self_correction", "rating": "consistent"}],
                "next_session_direction": "Complete PFR Session 1b with a distinct item set.",
                "home_practice_suggestion": "Read the five assigned words once.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["position_code"], "PFR-A-01")
        self.assertEqual(response.data["intervention_part"], "pfr_1a")
        self.assertEqual(response.data["accuracy_rate"], "90.00")
        self.assertEqual(response.data["revision_history"][0]["snapshot"]["targeted_position_ids"], [self.position_one.id])

    def test_specialist_can_override_the_recommended_methodology(self):
        og_curriculum = Curriculum.objects.create(
            center=self.center,
            code=Curriculum.Code.OG_PLUS,
            name="OG+",
        )
        og_position = CurriculumSequence.objects.create(
            center=self.center,
            curriculum=og_curriculum,
            code="OG-001",
            sequence_order=1,
            concept_number=1,
            title="Concept 1",
            position_type=CurriculumSequence.PositionType.PHONOLOGICAL_AWARENESS,
        )
        evidence = self._evidence()
        recommendation = PlacementRecommendation.objects.create(
            center=self.center,
            evidence=evidence,
            recommended_curriculum=self.curriculum,
            recommended_position=self.position_one,
            decision=PlacementRecommendation.Decision.PLACE,
            rationale="PFR placement rule result.",
            rule_trace={"rule": "test"},
        )

        response = self.client.post(
            f"/api/v1/placement-recommendations/{recommendation.id}/confirm/",
            {
                "final_position": og_position.id,
                "override_rationale": "The specialist reviewed complete OG+ concept-linked evidence.",
                "evidence_considered": {"og_item_set_ids": ["og-review-3"]},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, PlacementRecommendation.Status.OVERRIDDEN)
        self.assertEqual(recommendation.final_curriculum, og_curriculum)
        self.assertEqual(recommendation.resulting_placement.curriculum, og_curriculum)
        self.assertEqual(
            list(recommendation.materialized_sequence_plan.items.values_list("position_id", flat=True)),
            [og_position.id],
        )
