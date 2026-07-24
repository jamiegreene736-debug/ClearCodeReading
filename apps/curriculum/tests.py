from django.test import SimpleTestCase

from apps.curriculum.models import (
    ChildLessonAssignment,
    Curriculum,
    CurriculumSequence,
    LessonTemplate,
    Skill,
    StudentPlacement,
    StudentPlacementOverride,
    TeachingAid,
)
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
