from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.crm.models import Lead
from apps.users.models import AuditLog, Profile

User = get_user_model()


class AccountProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="parent-jamie",
            email="jamie@example.com",
            password="Clear-Code-123!",
            first_name="Jamie",
            last_name="Greene",
            role=User.Role.GUARDIAN,
        )
        self.client.force_login(self.user)

    def test_profile_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("account_profile"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_parent_can_save_contact_details_and_menu_uses_preferred_name(self):
        response = self.client.post(
            reverse("account_profile"),
            {
                "save": "profile",
                "display_name": "Jay",
                "first_name": "Jamie",
                "last_name": "Greene",
                "phone_number": "407-555-0199",
                "preferred_contact_method": "text",
                "timezone": "America/Chicago",
                "organization_name": "Lakeview Elementary",
                "job_title": "Parent",
                "city": "Orlando",
                "region": "FL",
                "postal_code": "32801",
                "about": "Afternoon texts work best.",
            },
        )
        self.assertRedirects(response, reverse("account_profile"))
        self.user.refresh_from_db()
        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.display_name, "Jay")
        self.assertEqual(profile.preferred_contact_method, "text")
        self.assertEqual(profile.timezone, "America/Chicago")
        self.assertEqual(profile.organization_name, "Lakeview Elementary")
        self.assertEqual(profile.city, "Orlando")
        self.assertEqual(self.user.phone_number, "407-555-0199")
        self.assertTrue(AuditLog.objects.filter(actor=self.user, action="account.profile_updated").exists())

        page = self.client.get(reverse("account_profile"))
        self.assertContains(page, 'data-testid="account-menu-name"')
        self.assertContains(page, "Jay")

    def test_save_updates_linked_crm_contact_without_creating_one(self):
        lead = Lead.objects.create(
            school_name="Lakeview",
            contact_name="Old Name",
            contact_email="jamie@example.com",
            audience=Lead.PipelineCategory.FAMILY_ENROLLMENT,
        )
        self.client.post(
            reverse("account_profile"),
            {
                "save": "profile",
                "display_name": "",
                "first_name": "Jamie",
                "last_name": "Greene",
                "phone_number": "407-555-0100",
                "preferred_contact_method": "email",
                "timezone": "America/New_York",
                "organization_name": "Greene Family",
                "job_title": "",
                "city": "",
                "region": "",
                "postal_code": "",
                "about": "",
            },
        )
        lead.refresh_from_db()
        self.assertEqual(lead.contact_name, "Jamie Greene")
        self.assertEqual(lead.contact_phone, "407-555-0100")
        self.assertEqual(lead.organization_name, "Greene Family")
        self.assertEqual(lead.linked_user, self.user)
        self.assertEqual(Lead.objects.count(), 1)

    def test_teacher_form_uses_school_labels(self):
        teacher = User.objects.create_user(
            username="teacher-ana",
            email="ana@example.com",
            password="Clear-Code-123!",
            first_name="Ana",
            role=User.Role.TEACHER,
        )
        self.client.force_login(teacher)
        page = self.client.get(reverse("account_profile"))
        self.assertContains(page, "School or organization")
        self.assertContains(page, "Role at your school")
        self.assertContains(page, "families and the ClearCode team")

    def test_password_change_keeps_the_session(self):
        response = self.client.post(
            reverse("account_profile"),
            {
                "save": "password",
                "old_password": "Clear-Code-123!",
                "new_password1": "A-Newer-Password-456!",
                "new_password2": "A-Newer-Password-456!",
            },
        )
        self.assertRedirects(response, reverse("account_profile"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("A-Newer-Password-456!"))
        self.assertEqual(self.client.get(reverse("account_profile")).status_code, 200)

    def test_invalid_profile_does_not_clear_existing_details(self):
        profile = self.user.profile
        profile.city = "Orlando"
        profile.save(update_fields=["city", "updated_at"])
        response = self.client.post(
            reverse("account_profile"),
            {
                "save": "profile",
                "display_name": "",
                "first_name": "",
                "last_name": "Greene",
                "phone_number": "",
                "preferred_contact_method": "email",
                "timezone": "America/New_York",
                "organization_name": "",
                "job_title": "",
                "city": "Tampa",
                "region": "",
                "postal_code": "",
                "about": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.city, "Orlando")
