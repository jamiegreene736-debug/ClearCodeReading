from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.crm.models import Lead
from apps.users.models import ChildProfile, GuardianRelationship


class ProgramPageAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(
            username="program-admin",
            email="program-admin@example.com",
            password="test-password",
            role=User.Role.SUPER_ADMIN,
        )
        cls.teacher = User.objects.create_user(
            username="program-teacher",
            email="program-teacher@example.com",
            password="test-password",
            role=User.Role.TEACHER,
            first_name="Ada",
            last_name="Teacher",
        )
        cls.parent = User.objects.create_user(
            username="program-parent",
            email="program-parent@example.com",
            password="test-password",
            role=User.Role.GUARDIAN,
        )
        cls.child = ChildProfile.objects.create(first_name="Avery", last_name="Reader")

    def test_staff_pages_are_real_routes_for_admins(self):
        self.client.force_login(self.admin)
        for name in (
            "portal_readers",
            "portal_sessions",
            "portal_placements",
            "portal_results",
            "portal_lesson_library",
            "portal_teacher_assignments",
            "portal_invitations",
        ):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
        dashboard = self.client.get(reverse("portal_dashboard"))
        self.assertContains(dashboard, reverse("portal_readers"))
        self.assertContains(dashboard, reverse("portal_teacher_assignments"))
        self.assertNotContains(dashboard, 'id="session-launchpad"')
        self.assertNotContains(dashboard, 'id="latest-kpis"')

    def test_teacher_can_open_instruction_pages_but_not_admin_queues(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse("portal_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("portal_readers")).status_code, 200)
        self.assertEqual(self.client.get(reverse("portal_sessions")).status_code, 200)
        self.assertEqual(self.client.get(reverse("portal_results")).status_code, 200)
        self.assertEqual(self.client.get(reverse("portal_lesson_library")).status_code, 302)
        self.assertEqual(self.client.get(reverse("portal_invitations")).status_code, 302)

    def test_parent_results_stay_available_and_staff_tools_do_not(self):
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(reverse("portal_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("portal_results")).status_code, 200)
        readers = self.client.get(reverse("portal_readers"))
        self.assertEqual(readers.status_code, 302)
        self.assertEqual(readers.url, reverse("portal_dashboard"))

    def test_teacher_assignment_lives_on_the_readers_page(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("assign_teacher"),
            {
                "child_id": self.child.id,
                "teacher_id": self.teacher.id,
            },
        )
        self.assertRedirects(response, reverse("portal_teacher_assignments"))
        self.child.refresh_from_db()
        self.assertEqual(self.child.learning_profile["assigned_teacher_id"], self.teacher.id)
        page = self.client.get(reverse("portal_readers"))
        self.assertContains(page, "Avery Reader")
        self.assertContains(page, "Ada Teacher")

    def test_reader_links_to_the_family_crm_contact(self):
        guardian = get_user_model().objects.create_user(
            username="family-guardian",
            email="family@example.com",
            password="test-password",
            role=get_user_model().Role.GUARDIAN,
        )
        GuardianRelationship.objects.create(
            guardian=guardian,
            child=self.child,
            relationship_type=GuardianRelationship.RelationshipType.PARENT,
        )
        lead = Lead.objects.create(
            school_name="ClearCode",
            contact_name="Family Guardian",
            contact_email="family@example.com",
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("portal_readers"))
        self.assertContains(response, reverse("crm_contact_detail", args=[lead.pk]))

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        USER_INVITATIONS_ALLOW_TEST_EMAIL=True,
        CRM_EMAIL_ENABLED=False,
        PUBLIC_APP_URL="https://testserver",
    )
    def test_invitation_queue_filters_sent_invitations(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("manage_users"),
            {"first_name": "New", "email": "invited@example.com", "role": "teacher"},
        )
        waiting = self.client.get(reverse("portal_invitations") + "?status=sent")
        self.assertContains(waiting, "invited@example.com")
        finished = self.client.get(reverse("portal_invitations") + "?status=accepted")
        self.assertNotContains(finished, "invited@example.com")
