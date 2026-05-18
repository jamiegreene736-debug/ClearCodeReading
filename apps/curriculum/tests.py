from django.test import SimpleTestCase

from apps.curriculum.models import ChildLessonAssignment, LessonTemplate, Skill, TeachingAid
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
