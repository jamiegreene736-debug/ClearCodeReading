from django.test import TestCase
from django.urls import reverse

from apps.crm.models import Lead
from apps.crm_email.models import EmailTemplate, Message
from apps.crm_email.referral_templates import REFERRAL_TEMPLATES
from apps.users.models import CustomUser


class TemplateLibraryTests(TestCase):
    def setUp(self) -> None:
        self.user = CustomUser.objects.create_user(
            username="library",
            email="library@example.com",
            role=CustomUser.Role.CRM_USER,
        )
        self.other = CustomUser.objects.create_user(
            username="other-library",
            email="other-library@example.com",
            role=CustomUser.Role.CRM_USER,
        )
        self.client.force_login(self.user)
        self.url = reverse("crm_email_templates")

    def test_library_lists_picker_sources_and_keeps_personal_templates_private(
        self,
    ) -> None:
        own = EmailTemplate.objects.create(
            owner=self.user,
            name="My welcome",
            subject="Welcome",
            body_html="<p>Hello</p>",
        )
        EmailTemplate.objects.create(
            owner=self.other,
            name="Private other message",
            subject="Secret",
            body_html="<p>Hidden</p>",
        )
        response = self.client.get(self.url)
        self.assertContains(response, "Shared pipeline templates")
        self.assertContains(response, REFERRAL_TEMPLATES["referral-general"].name)
        self.assertContains(response, reverse("crm_email_template", args=[own.pk]))
        self.assertNotContains(response, "Private other message")
        self.assertNotContains(response, "Edit shared wording")
        self.assertContains(response, "Ask a CRM administrator")
        self.assertContains(response, 'aria-current="page">Email templates')
        self.user.is_superuser = True
        self.user.save()
        response = self.client.get(self.url)
        self.assertContains(response, "Edit shared wording")

    def test_save_edit_and_delete_return_to_library(self) -> None:
        response = self.client.post(
            reverse("crm_email_template_new"),
            {"name": "Welcome", "subject": "Hello", "body_html": "<p>Welcome</p>"},
        )
        self.assertRedirects(response, self.url)
        template = EmailTemplate.objects.get(owner=self.user)
        url = reverse("crm_email_template", args=[template.pk])
        self.assertRedirects(
            self.client.post(
                url,
                {
                    "name": "Revised",
                    "subject": "Hello again",
                    "body_html": "<p>Revised</p>",
                },
            ),
            self.url,
        )
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {"action": "delete"}).status_code, 404)
        self.client.force_login(self.user)
        self.assertRedirects(self.client.post(url, {"action": "delete"}), self.url)
        self.assertFalse(EmailTemplate.objects.exists())

    def test_referral_copy_is_not_saved_until_submitted(self) -> None:
        url = reverse("crm_email_template_new") + "?source=referral-pediatrician"
        response = self.client.get(url)
        self.assertFalse(EmailTemplate.objects.exists())
        initial = response.context["form"].initial
        self.assertIn("{{contact.name}}", initial["body_html"])
        self.assertNotIn("{{contact.doctor_name}}", initial["body_html"])
        self.assertRedirects(self.client.post(url, initial), self.url)
        self.assertEqual(EmailTemplate.objects.get().owner, self.user)
        self.assertEqual(
            self.client.get(
                reverse("crm_email_template_new") + "?source=unknown"
            ).status_code,
            404,
        )
        self.assertFalse(Message.objects.exists())

    def test_settings_and_compose_link_to_library(self) -> None:
        lead = Lead.objects.create(
            contact_name="Guest", contact_email="guest@example.com"
        )
        for url in (
            reverse("crm_email_settings"),
            reverse("crm_email_compose", args=[lead.pk]),
        ):
            response = self.client.get(url)
            self.assertContains(response, self.url)
        response = self.client.get(reverse("crm_email_compose", args=[lead.pk]))
        self.assertContains(response, "opens in a new tab")
        self.assertFalse(Message.objects.exists())

    def test_library_requires_crm_access(self) -> None:
        self.user.role = CustomUser.Role.GUARDIAN
        self.user.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
