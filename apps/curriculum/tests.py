from django.test import SimpleTestCase

from apps.ai.services import DisabledInstructionalAIService
from apps.curriculum.models import (
    ChildLessonAssignment,
    Curriculum,
    CurriculumSequence,
    LessonTemplate,
    PlacementEvidence,
    PlacementRecommendation,
    Skill,
    StudentPlacement,
    StudentPlacementOverride,
    TeachingAid,
)
from apps.curriculum.placement import score_og_placement, score_pfr_placement
from apps.curriculum.serializers import ChildLessonAssignmentSerializer, LessonSerializer, LessonTemplateSerializer, SkillSerializer


class CurriculumTests(SimpleTestCase):
    def test_core_reading_skill_domains_exist(self):
        self.assertIn(Skill.Domain.PHONICS, Skill.Domain.values)
        self.assertIn(Skill.Domain.COMPREHENSION, Skill.Domain.values)

    def test_teaching_aid_types_include_decodable_text(self):
        self.assertIn(TeachingAid.AidType.DECODABLE_TEXT, TeachingAid.AidType.values)

    def test_lesson_serializer_has_personalization_fields(self):
        fields = LessonSerializer().fields
        self.assertIn("skill_detail", fields)
        self.assertIn("teaching_aids", fields)

    def test_skill_serializer_has_prerequisite_details(self):
        self.assertIn("prerequisite_details", SkillSerializer().fields)

    def test_lesson_templates_support_teacher_assignment(self):
        self.assertIn("assigned", ChildLessonAssignment.Status.values)
        self.assertTrue(LessonTemplate._meta.get_field("activities").default is list)

    def test_lesson_assignment_serializers_include_portal_fields(self):
        self.assertIn("activities", LessonTemplateSerializer().fields)
        self.assertIn("teacher_notes", ChildLessonAssignmentSerializer().fields)

    def test_legacy_skill_remains_available(self):
        self.assertEqual(Skill.__doc__, "Legacy generic skill taxonomy retained for existing API compatibility.")

    def test_curriculum_choices_are_methodology_specific(self):
        self.assertEqual(set(Curriculum.Code.values), {"pfr", "og_plus"})

    def test_sequence_supports_pfr_and_og_coordinates(self):
        field_names = {field.name for field in CurriculumSequence._meta.get_fields()}
        self.assertTrue({"level", "lesson_number", "concept_number", "prerequisites"}.issubset(field_names))

    def test_placement_has_explicit_override_history(self):
        self.assertEqual(
            StudentPlacementOverride._meta.get_field("placement").remote_field.related_name,
            "override_history",
        )
        self.assertIn("methodology_rationale", {field.name for field in StudentPlacement._meta.get_fields()})

    def test_pfr_placement_uses_timeout_and_ceiling_rules(self):
        decision = score_pfr_placement(
            {
                "parts": [
                    {
                        "position_code": "PFR-A-01",
                        "items": [
                            {"item_id": str(index), "correct": True, "latency_seconds": 3}
                            for index in range(8)
                        ]
                        + [{"item_id": "8", "correct": False}, {"item_id": "9", "correct": False}],
                    },
                    {
                        "position_code": "PFR-A-02",
                        "items": [
                            {"item_id": "1", "correct": True, "latency_seconds": 6},
                            {"item_id": "2", "correct": False},
                            {"item_id": "3", "correct": False},
                            {"item_id": "4", "correct": False},
                            {"item_id": "5", "correct": True},
                        ],
                    },
                ]
            },
            ["PFR-A-01", "PFR-A-02", "PFR-A-03"],
        )
        self.assertEqual(decision.position_code, "PFR-A-02")
        self.assertEqual(decision.rule_trace["basal"], "PFR-A-01")
        self.assertEqual(decision.rule_trace["ceiling"], "PFR-A-02")
        self.assertTrue(decision.rule_trace["parts"][1]["terminated_after_four_errors"])

    def test_pfr_no_basal_returns_first_lesson(self):
        decision = score_pfr_placement(
            {
                "parts": [
                    {
                        "position_code": "PFR-A-02",
                        "items": [{"item_id": str(index), "correct": index < 3} for index in range(7)],
                    }
                ]
            },
            ["PFR-A-01", "PFR-A-02"],
        )
        self.assertEqual(decision.position_code, "PFR-A-01")
        self.assertEqual(decision.deficit_profile[0]["code"], "no_basal")

    def test_pfr_multisyllabic_word_requires_every_part(self):
        decision = score_pfr_placement(
            {
                "parts": [
                    {
                        "position_code": "PFR-A-01",
                        "items": [
                            {
                                "item_id": "multi-1",
                                "correct": True,
                                "parts": [
                                    {"part_id": "multi-1-a", "correct": True},
                                    {"part_id": "multi-1-b", "correct": False},
                                ],
                            }
                        ],
                    }
                ]
            },
            ["PFR-A-01"],
        )
        self.assertEqual(decision.rule_trace["parts"][0]["correct"], 0)
        self.assertEqual(decision.position_code, "PFR-A-01")

    def test_og_placement_applies_decoding_and_encoding_thresholds(self):
        decision = score_og_placement(
            {
                "concepts": [
                    {
                        "position_code": "OG-001",
                        "decoding": {"correct": 9, "total": 10},
                        "encoding": {"correct": 9, "total": 10},
                    },
                    {
                        "position_code": "OG-002",
                        "decoding": {"correct": 9, "total": 10},
                        "encoding": {"correct": 8, "total": 10},
                    },
                ]
            },
            ["OG-001", "OG-002", "OG-003"],
            PlacementEvidence.Instrument.OG_BENCHMARK,
        )
        self.assertEqual(decision.position_code, "OG-002")
        self.assertEqual(decision.deficit_profile[0]["encoding_accuracy"], 80)

    def test_og_incomplete_evidence_requires_specialist_review(self):
        decision = score_og_placement(
            {"concepts": [{"position_code": "OG-001"}]},
            ["OG-001"],
            PlacementEvidence.Instrument.OG_PA_DIAGNOSTIC,
        )
        self.assertEqual(decision.decision, PlacementRecommendation.Decision.SPECIALIST_REVIEW)

    def test_override_is_labeled_with_evidence_and_source(self):
        field_names = {field.name for field in StudentPlacementOverride._meta.get_fields()}
        self.assertTrue({"evidence_considered", "source_recommendation"}.issubset(field_names))

    def test_default_ai_service_cannot_apply_instructional_changes(self):
        self.assertIsNone(DisabledInstructionalAIService().placement_narrative({"deficit_profile": []}))
