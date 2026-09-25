from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from apps.curriculum.models import LessonTemplate, Skill, TeacherLessonTemplate
from apps.schools.models import School
from apps.users.models import ChildProfile, CustomUser


class InstructionWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        original = School.auto_create_schema
        School.auto_create_schema = False
        try:
            cls.center = School.objects.create(name="Assignment Center", slug="assignment-center", schema_name="assignment_center")
        finally:
            School.auto_create_schema = original
        cls.admin = CustomUser.objects.create_user(
            username="instruction-admin",
            email="instruction-admin@example.com",
            password="password",
            role=CustomUser.Role.SUPER_ADMIN,
        )
        cls.teacher_user = CustomUser.objects.create_user(
            username="instruction-teacher",
            email="instruction-teacher@example.com",
            password="password",
            role=CustomUser.Role.TEACHER,
            first_name="Mina",
            last_name="Wells",
        )
        cls.reader = ChildProfile.objects.create(first_name="Avery", last_name="Reader", school=cls.center)
        cls.skill = Skill.objects.create(code="PH-ASSIGN", name="Short vowels", domain=Skill.Domain.PHONICS)

    def test_teacher_cannot_open_administrator_workspaces(self):
        self.client.force_login(self.teacher_user)
        for name in ("portal_teacher_assignments", "portal_lesson_library"):
            response = self.client.get(reverse(name))
            self.assertRedirects(response, reverse("portal_dashboard"))

    def test_admin_creates_a_teacher_assignment_on_its_own_page(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("portal_teacher_assignments"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Create an assignment")
        self.assertContains(page, "Avery Reader")
        self.assertContains(page, "Needs a teacher")

        response = self.client.post(
            reverse("assign_teacher"),
            {"child_id": self.reader.id, "teacher_id": self.teacher_user.id},
        )
        self.assertRedirects(response, reverse("portal_teacher_assignments"))
        self.reader.refresh_from_db()
        self.assertEqual(str(self.reader.learning_profile["assigned_teacher_id"]), str(self.teacher_user.id))

        filtered = self.client.get(reverse("portal_teacher_assignments"), {"status": "assigned", "q": "Avery"})
        self.assertContains(filtered, "Mina Wells")
        self.assertContains(filtered, "Save assignment")

    def test_admin_creates_and_shares_a_lesson(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("portal_lesson_library"))
        self.assertContains(page, "Create a lesson")
        self.assertContains(page, "Give a teacher access")

        created = self.client.post(
            reverse("portal_create_lesson"),
            {
                "title": "Blend and Read",
                "grade_band": "1-2",
                "recommended_minutes": "12",
                "skill_id": self.skill.id,
                "goal": "Blend three sounds into a word.",
                "description": "A short decoding routine.",
                "activities": "Say each sound.\nBlend the word.",
                "materials": "Letter cards",
            },
        )
        self.assertRedirects(created, reverse("portal_lesson_library"))
        lesson = LessonTemplate.objects.get(title="Blend and Read")
        self.assertEqual(lesson.activities, ["Say each sound.", "Blend the word."])
        self.assertEqual(lesson.skill, self.skill)

        shared = self.client.post(
            reverse("portal_assign_template_to_teacher"),
            {"teacher_id": self.teacher_user.id, "template_id": lesson.id, "notes": "Use before fluency work."},
        )
        self.assertRedirects(shared, reverse("portal_lesson_library"))
        self.assertTrue(
            TeacherLessonTemplate.objects.filter(teacher=self.teacher_user, template=lesson, is_deleted=False).exists()
        )
        library = self.client.get(reverse("portal_lesson_library"), {"q": "Blend", "grade": "1-2"})
        self.assertContains(library, "Blend and Read")
        self.assertContains(library, "Mina Wells")

    def test_invalid_lesson_time_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("portal_create_lesson"),
            {"title": "Too long", "recommended_minutes": "400"},
        )
        self.assertRedirects(response, reverse("portal_lesson_library"))
        self.assertFalse(LessonTemplate.objects.filter(title="Too long").exists())
        self.assertIn("1 and 180", "; ".join(str(message) for message in get_messages(response.wsgi_request)))
