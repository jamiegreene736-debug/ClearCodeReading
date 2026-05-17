from django.test import SimpleTestCase

from apps.curriculum.models import Skill, TeachingAid
from apps.curriculum.serializers import LessonSerializer, SkillSerializer


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
