from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.progress.models import OgConceptResources, OgFamilyPlan, OgWeeklyAssessment
from apps.schools.models import School, SchoolMembership
from apps.users.models import ChildProfile, ConsentLog, CustomUser, GuardianRelationship


class OgWeeklyWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        original = School.auto_create_schema
        School.auto_create_schema = False
        try:
            school = School.objects.create(name="Test Center", slug="og-test", schema_name="og_test")
        finally:
            School.auto_create_schema = original
        cls.teacher = CustomUser.objects.create_user("test-teacher", email="og-teacher@example.com", role=CustomUser.Role.TEACHER)
        cls.unassigned = CustomUser.objects.create_user("another-teacher", email="og-another@example.com", role=CustomUser.Role.TEACHER)
        cls.parent = CustomUser.objects.create_user("test-parent", email="og-parent@example.com", role=CustomUser.Role.GUARDIAN)
        for teacher in (cls.teacher, cls.unassigned):
            SchoolMembership.objects.create(school=school, user=teacher, role=SchoolMembership.Role.TEACHER)
        cls.child = ChildProfile.objects.create(
            first_name="Test Reader", school=school, learning_profile={"assigned_teacher_id": str(cls.teacher.pk)}
        )
        relationship = GuardianRelationship.objects.create(
            guardian=cls.parent, child=cls.child, consent_status=GuardianRelationship.ConsentStatus.GRANTED,
            relationship_type=GuardianRelationship.RelationshipType.PARENT, permissions={"progress_dashboard": True},
        )
        for consent_type in ("assessment", "data_processing", "school_sharing"):
            ConsentLog.objects.create(
                guardian_relationship=relationship, guardian=cls.parent, child=cls.child,
                consent_type=consent_type, status=ConsentLog.Status.GRANTED,
            )
        cls.plan = OgFamilyPlan.objects.create(child=cls.child, excluded_concepts=[27])
        cls.pack = OgConceptResources.objects.create(
            plan=cls.plan, concept=26,
            connection_letter="https://drive.google.com/file/d/test-letter/view",
            fluency_practice="https://drive.google.com/file/d/test-fluency/view",
            decodable_passage="https://drive.google.com/file/d/test-passage/view",
        )

    def setUp(self):
        self.url = reverse("og_weekly_assessment", args=[self.child.pk])
        self.data = {
            "assessed_on": timezone.localdate().isoformat(), "concept": "26",
            "word_spelling": "4/5", "red_words": "2/2", "capitalization": "2/3", "spacing": "yes",
            "punctuation": "1/2", "sentence_spelling": "13/15", "fluency_title": "Test fluency",
            "wcpm": "46", "fluency_total": "50", "comprehension_title": "Test comprehension",
            "comprehension": "4/5", "concept_passed": "on", "word_notes": "Private teacher note.",
        }

    def parent_state(self):
        client = APIClient()
        client.force_authenticate(self.parent)
        response = client.get(f"/api/v1/progress/dashboard/?child={self.child.pk}")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["og_plus"]

    def test_family_resources_unlock_only_after_pass_and_concept_27_is_skipped(self):
        before = self.parent_state()
        self.assertEqual(before["current_concept"], 26)
        self.assertEqual(before["resource_packs"][0]["links"], [])
        self.client.force_login(self.teacher)
        page = self.client.get(self.url)
        self.assertContains(page, "OG+ weekly assessment")
        self.assertNotContains(page, ">Concept 27<")
        self.assertRedirects(self.client.post(self.url, self.data), self.url)
        after = self.parent_state()
        self.assertEqual(after["current_concept"], 28)
        self.assertEqual(after["progress_report"]["accuracy"], 92)
        self.assertEqual(len(after["resource_packs"][0]["links"]), 3)
        self.assertNotIn("Private teacher note", str(after))
        self.client.force_login(self.parent)
        parent_page = self.client.get(reverse("portal_dashboard"))
        self.assertContains(parent_page, "Current concept: 28")
        self.assertContains(parent_page, self.pack.connection_letter)

    def test_cannot_record_as_an_unassigned_teacher_or_as_a_parent(self):
        for user in (self.parent, self.unassigned):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        self.assertFalse(OgWeeklyAssessment.objects.exists())

    def test_rejects_bad_fraction_and_early_pass(self):
        self.client.force_login(self.teacher)
        self.assertContains(self.client.post(self.url, {**self.data, "word_spelling": "5/4"}), "number attempted")
        self.assertContains(
            self.client.post(self.url, {**self.data, "concept": "28"}), "Only the current concept"
        )
        self.assertFalse(OgWeeklyAssessment.objects.exists())

    def test_saving_a_check_does_not_automatically_unlock_it(self):
        self.client.force_login(self.teacher)
        self.assertRedirects(self.client.post(self.url, {**self.data, "concept_passed": ""}), self.url)
        state = self.parent_state()
        self.assertEqual(state["current_concept"], 26)
        self.assertFalse(state["resource_packs"][0]["unlocked"])
