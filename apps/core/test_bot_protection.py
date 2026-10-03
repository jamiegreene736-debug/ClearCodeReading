import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.core.bot_protection import (
    BURST_MESSAGE,
    HUMAN_MESSAGE,
    BotVerdict,
    bot_verdict,
    burst_cache_key,
    client_address,
    issue_human_token,
)
from apps.core.models import RecruitingInterest
from apps.crm.models import FormSubmission, Lead, NewsletterSubscription
from apps.crm.views import FAMILY_RESOURCES_SESSION_KEY


def _token_from(html: str) -> str:
    match = re.search(r'name="human_check" value="([^"]+)"', html)
    if match is None:
        raise AssertionError("Rendered form did not include a human-check token.")
    return match.group(1)


class PublicFormBotProtectionTests(TestCase):
    def setUp(self):
        cache.delete(burst_cache_key("signup", "127.0.0.1"))
        cache.delete(burst_cache_key("survey", "127.0.0.1"))

    def _contact_payload(self, **overrides):
        payload = {
            "name": "Jamie Reader",
            "email": "jamie-reader@example.com",
            "audience": "parent",
            "organization_name": "Website contact",
            "notes": "I would like to talk about reading support.",
            "redirect_to": "/contact/",
            "website": "",
            "human_check": issue_human_token("signup"),
        }
        payload.update(overrides)
        return payload

    def _survey_payload(self, **overrides):
        payload = {
            "source_path": "/survey/",
            "name": "Jordan Reader",
            "email": "jordan-reader@example.com",
            "home_zip": "32789",
            "respondent_situation": "grade_3_5_struggling",
            "commitment_preference": "yes_have_time",
            "engagement_interests": ["priority_waitlist"],
            "website": "",
            "human_check": issue_human_token("survey"),
        }
        payload.update(overrides)
        return payload

    def test_rendered_contact_form_submits_for_a_person(self):
        page = self.client.get(reverse("marketing_contact"))
        html = page.content.decode()
        self.assertIn('name="website"', html)
        token = _token_from(html)

        response = self.client.post(
            reverse("crm_signup"),
            self._contact_payload(human_check=token, email="person-contact@example.com"),
        )

        self.assertRedirects(
            response,
            "/contact/?signup=thanks#consultation-form",
            fetch_redirect_response=False,
        )
        self.assertTrue(Lead.objects.filter(contact_email="person-contact@example.com").exists())

    def test_empty_honeypot_without_human_check_cannot_submit_contact_survey_or_support(self):
        contact = self.client.post(
            reverse("crm_signup"),
            self._contact_payload(human_check="", email="empty-contact@example.com"),
        )
        support = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Bot",
                "email": "empty-support@example.com",
                "audience": "parent",
                "organization_name": "ClearCode support",
                "support_topic": "technical",
                "notes": "automated support request",
                "redirect_to": "/support/",
                "website": "",
                "human_check": "",
            },
        )
        resources = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Bot",
                "email": "empty-resources@example.com",
                "audience": "parent",
                "redirect_to": "/resources/",
                "website": "",
                "human_check": "",
            },
        )
        survey = self.client.post(
            reverse("crm_survey_submit"),
            self._survey_payload(human_check="", email="empty-survey@example.com"),
        )
        newsletter = self.client.post(
            reverse("newsletter_signup"),
            {
                "email": "empty-news@example.com",
                "consent": "yes",
                "redirect_to": "/",
                "website": "",
                "human_check": "",
            },
        )

        self.assertEqual(contact.status_code, 302)
        self.assertIn("signup=invalid", contact.url)
        self.assertIn("signup=invalid", support.url)
        self.assertIn("signup=invalid", resources.url)
        self.assertIn("survey=invalid", survey.url)
        self.assertIn("newsletter=invalid", newsletter.url)
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(FormSubmission.objects.exists())
        self.assertFalse(NewsletterSubscription.objects.exists())
        self.assertNotIn(FAMILY_RESOURCES_SESSION_KEY, self.client.session)
        followed = self.client.get(contact.url)
        self.assertContains(followed, HUMAN_MESSAGE)

    def test_filled_honeypot_is_discarded_even_with_a_valid_human_check(self):
        response = self.client.post(
            reverse("crm_survey_submit"),
            self._survey_payload(website="https://spam.example", email="filled-honeypot@example.com"),
        )

        self.assertRedirects(
            response,
            "/survey/?survey=thanks#early-interest-survey",
            fetch_redirect_response=False,
        )
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(FormSubmission.objects.exists())

        career = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Bot Applicant",
                "email": "career-bot@example.com",
                "phone": "555-0100",
                "address": "1 Spam Street",
                "how_heard": "web",
                "career_path": "teacher",
                "redirect_to": "/careers/",
                "website": "https://spam.example",
                "human_check": issue_human_token("signup"),
            },
        )
        self.assertEqual(career.status_code, 302)
        self.assertFalse(RecruitingInterest.objects.exists())

    def test_survey_token_does_not_authorize_another_form(self):
        page = self.client.get(reverse("early_interest_survey"))
        survey_token = _token_from(page.content.decode())
        response = self.client.post(
            reverse("crm_signup"),
            self._contact_payload(human_check=survey_token, email="wrong-scope@example.com"),
        )

        self.assertIn("signup=invalid", response.url)
        self.assertFalse(Lead.objects.filter(contact_email="wrong-scope@example.com").exists())

    @override_settings(PUBLIC_FORM_BURST_LIMIT=2)
    def test_burst_of_repeats_is_rejected(self):
        cache.delete(burst_cache_key("signup", "127.0.0.1"))
        created = []
        for index in range(3):
            email = f"burst-{index}@example.com"
            response = self.client.post(
                reverse("crm_signup"),
                self._contact_payload(email=email, human_check=issue_human_token("signup")),
            )
            created.append((email, response))

        self.assertIn("signup=thanks", created[0][1].url)
        self.assertIn("signup=thanks", created[1][1].url)
        self.assertIn("signup=invalid", created[2][1].url)
        self.assertTrue(Lead.objects.filter(contact_email="burst-0@example.com").exists())
        self.assertTrue(Lead.objects.filter(contact_email="burst-1@example.com").exists())
        self.assertFalse(Lead.objects.filter(contact_email="burst-2@example.com").exists())
        self.assertContains(self.client.get(created[2][1].url), BURST_MESSAGE)

    def test_rendered_survey_still_submits(self):
        page = self.client.get(reverse("early_interest_survey"))
        token = _token_from(page.content.decode())
        response = self.client.post(
            reverse("crm_survey_submit"),
            self._survey_payload(human_check=token, email="person-survey@example.com"),
            follow=True,
        )

        self.assertContains(response, "Thank you!")
        self.assertTrue(Lead.objects.filter(contact_email="person-survey@example.com").exists())

    def test_login_page_token_lets_a_person_sign_in_and_blocks_a_missing_check(self):
        user = get_user_model().objects.create_user(
            username="reader",
            email="reader-login@example.com",
            password="A-real-password-123",
        )
        page = self.client.get(reverse("login"))
        token = _token_from(page.content.decode())
        signed_in = self.client.post(
            reverse("login"),
            {
                "username": user.email,
                "password": "A-real-password-123",
                "website": "",
                "human_check": token,
            },
        )
        self.assertEqual(signed_in.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))

        self.client.logout()
        blocked = self.client.post(
            reverse("login"),
            {
                "username": user.email,
                "password": "A-real-password-123",
                "website": "",
                "human_check": "",
            },
        )
        self.assertEqual(blocked.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_omitted_honeypot_and_human_check_are_bot_verdicts(self):
        factory = RequestFactory()
        empty_honeypot = factory.post(
            "/crm/signup/",
            {
                "name": "Bot",
                "email": "bot@example.com",
                "notes": "hello",
                "redirect_to": "/contact/",
                "website": "",
            },
        )
        self.assertEqual(bot_verdict(empty_honeypot, "signup"), BotVerdict.HUMAN)
        self.assertEqual(client_address(empty_honeypot), "127.0.0.1")

        filled = factory.post("/crm/survey/", {"source_path": "/survey/", "website": "https://spam.example"})
        self.assertEqual(bot_verdict(filled, "survey"), BotVerdict.HONEYPOT)

    def test_less_visible_public_forms_render_a_human_check(self):
        pages = {
            "marketing_contact": "signup",
            "marketing_support": "signup",
            "marketing_careers": "signup",
            "marketing_resources": "signup",
            "early_interest_survey": "survey",
            "reading_assessment": "signup",
            "login": "login",
        }
        for route_name in pages:
            response = self.client.get(reverse(route_name))
            html = response.content.decode()
            with self.subTest(route_name=route_name):
                self.assertEqual(response.status_code, 200)
                self.assertIn('name="human_check"', html)
                self.assertIn('name="website"', html)

        root = Path(__file__).resolve().parents[2]
        for relative in (
            "templates/crm/consultation_booking.html",
            "templates/crm/inventory_intake.html",
            "templates/crm/inventory_public.html",
            "templates/crm/inventory_booking.html",
            "templates/crm/newsletter_unsubscribe.html",
            "templates/registration/accept_invitation.html",
            "templates/resources/signin.html",
            "templates/resources/setup.html",
            "marketing-website/_newsletter_signup.html",
        ):
            text = (root / relative).read_text()
            with self.subTest(relative=relative):
                self.assertTrue(
                    "bot_fields" in text or "bot_human_field" in text,
                    relative,
                )
