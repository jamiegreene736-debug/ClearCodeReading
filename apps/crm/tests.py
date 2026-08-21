from unittest.mock import MagicMock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.crm.admin import NewsletterCampaignAdmin
from apps.crm.models import (
    Lead,
    NewsletterCampaign,
    NewsletterDelivery,
    NewsletterSubscription,
    Opportunity,
)
from apps.crm.newsletters import make_unsubscribe_token, send_newsletter_campaign
from apps.crm.serializers import OpportunitySerializer
from apps.crm.views import NewsletterUnsubscribeView, WebsiteSignupView


class CrmTests(SimpleTestCase):
    def test_lead_pipeline_statuses_exist(self):
        self.assertIn(Lead.Status.NEW, Lead.Status.values)
        self.assertIn(Lead.Status.CONVERTED, Lead.Status.values)

    def test_opportunity_terminal_stages_exist(self):
        self.assertIn(Opportunity.Stage.WON, Opportunity.Stage.values)
        self.assertIn(Opportunity.Stage.LOST, Opportunity.Stage.values)

    def test_opportunity_probability_validation(self):
        serializer = OpportunitySerializer()
        with self.assertRaisesMessage(Exception, "Probability must be between 0 and 100."):
            serializer.validate_probability(101)

    def test_website_signup_defaults_family_inquiry_for_parent(self):
        self.assertEqual(
            WebsiteSignupView._school_name_for_signup(Lead.Audience.PARENT, ""),
            "Family inquiry",
        )

    def test_website_signup_cleans_positive_student_count(self):
        self.assertEqual(WebsiteSignupView._clean_positive_int("24"), 24)
        self.assertIsNone(WebsiteSignupView._clean_positive_int("-1"))
        self.assertIsNone(WebsiteSignupView._clean_positive_int("many"))

    def test_homepage_positions_clear_code_as_specialist_led_intervention(self):
        homepage = render_to_string("index.html")

        self.assertIn("Reading intervention that shows clear progress", homepage)
        self.assertIn("Specialist-led structured literacy", homepage)
        self.assertIn("Schedule a consultation", homepage)
        self.assertIn("Phonics for Reading", homepage)
        self.assertIn("IMSE Orton-Gillingham+", homepage)
        self.assertNotIn("4x more clarity", homepage)
        self.assertNotIn("For schools &amp; teachers", homepage)


class NewsletterSignupTests(TestCase):
    def test_signup_records_explicit_consent_and_normalizes_email(self):
        response = self.client.post(
            reverse("newsletter_signup"),
            {
                "name": "Jamie Reader",
                "email": "  Jamie@Example.COM ",
                "consent": "yes",
                "redirect_to": "/families/",
            },
        )

        self.assertRedirects(
            response,
            "/families/?newsletter=thanks#newsletter-signup",
            fetch_redirect_response=False,
        )
        subscription = NewsletterSubscription.objects.get()
        self.assertEqual(subscription.email, "jamie@example.com")
        self.assertEqual(subscription.name, "Jamie Reader")
        self.assertEqual(subscription.status, NewsletterSubscription.Status.ACTIVE)
        self.assertEqual(subscription.source_path, "/families/")
        self.assertIsNotNone(subscription.consented_at)

    def test_signup_rejects_invalid_email_or_missing_consent(self):
        invalid_email = self.client.post(
            reverse("newsletter_signup"),
            {"email": "not-an-email", "consent": "yes", "redirect_to": "/"},
        )
        missing_consent = self.client.post(
            reverse("newsletter_signup"),
            {"email": "reader@example.com", "redirect_to": "/"},
        )

        self.assertEqual(invalid_email.status_code, 302)
        self.assertEqual(missing_consent.status_code, 302)
        self.assertFalse(NewsletterSubscription.objects.exists())

    def test_signup_quietly_discards_honeypot_submission(self):
        response = self.client.post(
            reverse("newsletter_signup"),
            {
                "email": "bot@example.com",
                "consent": "yes",
                "website": "https://spam.example",
                "redirect_to": "/",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(NewsletterSubscription.objects.exists())

    def test_signup_reactivates_an_unsubscribed_address(self):
        old_consent = timezone.now() - timezone.timedelta(days=10)
        subscription = NewsletterSubscription.objects.create(
            email="reader@example.com",
            status=NewsletterSubscription.Status.UNSUBSCRIBED,
            consented_at=old_consent,
            unsubscribed_at=timezone.now(),
        )

        self.client.post(
            reverse("newsletter_signup"),
            {"email": "READER@example.com", "consent": "yes", "redirect_to": "/"},
        )

        subscription.refresh_from_db()
        self.assertEqual(subscription.status, NewsletterSubscription.Status.ACTIVE)
        self.assertIsNone(subscription.unsubscribed_at)
        self.assertGreater(subscription.consented_at, old_consent)

    def test_signup_does_not_allow_an_external_redirect(self):
        response = self.client.post(
            reverse("newsletter_signup"),
            {
                "email": "reader@example.com",
                "consent": "yes",
                "redirect_to": "https://attacker.example/phish",
            },
        )

        self.assertEqual(response.url, "/?newsletter=thanks#newsletter-signup")


class NewsletterUnsubscribeTests(TestCase):
    def setUp(self):
        self.subscription = NewsletterSubscription.objects.create(email="reader@example.com")
        self.token = make_unsubscribe_token(self.subscription)
        self.url = reverse("newsletter_unsubscribe", kwargs={"token": self.token})

    def test_get_requires_confirmation_and_post_unsubscribes(self):
        response = NewsletterUnsubscribeView.as_view()(
            RequestFactory().get(self.url),
            token=self.token,
        )
        self.subscription.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.subscription.status, NewsletterSubscription.Status.ACTIVE)

        response = NewsletterUnsubscribeView.as_view()(
            RequestFactory().post(self.url),
            token=self.token,
        )
        self.subscription.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIn("You’re unsubscribed.", response.content.decode())
        self.assertEqual(self.subscription.status, NewsletterSubscription.Status.UNSUBSCRIBED)
        self.assertIsNotNone(self.subscription.unsubscribed_at)

    def test_tampered_token_changes_nothing(self):
        tampered_token = f"{self.token[:-1]}{'a' if self.token[-1] != 'a' else 'b'}"
        tampered_url = reverse("newsletter_unsubscribe", kwargs={"token": tampered_token})
        response = NewsletterUnsubscribeView.as_view()(
            RequestFactory().post(tampered_url),
            token=tampered_token,
        )

        self.subscription.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIn("not valid", response.content.decode())
        self.assertEqual(self.subscription.status, NewsletterSubscription.Status.ACTIVE)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="ClearCode Reading <newsletter@example.com>",
    PUBLIC_APP_URL="https://clearcodereading.example",
)
class NewsletterSendingTests(TestCase):
    def setUp(self):
        self.active_one = NewsletterSubscription.objects.create(email="one@example.com", name="One")
        self.active_two = NewsletterSubscription.objects.create(email="two@example.com", name="Two")
        NewsletterSubscription.objects.create(
            email="opted-out@example.com",
            status=NewsletterSubscription.Status.UNSUBSCRIBED,
            unsubscribed_at=timezone.now(),
        )
        self.campaign = NewsletterCampaign.objects.create(
            subject="Reading practice that fits real life",
            preview_text="Three useful ideas for this week",
            body="Start with ten calm minutes.\n\nCelebrate careful decoding.",
        )

    def test_send_delivers_individually_to_active_snapshot_with_unsubscribe(self):
        result = send_newsletter_campaign(self.campaign.pk)

        self.assertEqual(result.status, NewsletterCampaign.Status.SENT)
        self.assertEqual(result.recipient_count, 2)
        self.assertEqual(result.delivered_count, 2)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual({message.to[0] for message in mail.outbox}, {"one@example.com", "two@example.com"})
        for message in mail.outbox:
            self.assertEqual(len(message.to), 1)
            self.assertIn("/newsletter/unsubscribe/", message.body)
            self.assertIn("List-Unsubscribe", message.extra_headers)
            self.assertEqual(message.extra_headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")

        send_newsletter_campaign(self.campaign.pk)
        self.assertEqual(len(mail.outbox), 2)

    def test_retry_sends_only_the_failed_delivery(self):
        message = MagicMock()
        message.send.side_effect = [1, RuntimeError("provider unavailable")]
        with patch("apps.crm.newsletters._delivery_message", return_value=message):
            first_result = send_newsletter_campaign(self.campaign.pk)

        self.assertEqual(first_result.status, NewsletterCampaign.Status.PARTIALLY_FAILED)
        self.assertEqual(first_result.delivered_count, 1)
        self.assertEqual(first_result.failed_count, 1)

        retry_result = send_newsletter_campaign(self.campaign.pk)

        self.assertEqual(retry_result.status, NewsletterCampaign.Status.SENT)
        self.assertEqual(retry_result.delivered_count, 2)
        self.assertEqual(retry_result.failed_count, 0)
        deliveries = NewsletterDelivery.objects.order_by("recipient_email")
        self.assertEqual([delivery.attempts for delivery in deliveries], [1, 2])
        self.assertEqual(len(mail.outbox), 1)

    def test_subscriber_who_opts_out_after_snapshot_is_skipped(self):
        delivery = NewsletterDelivery.objects.create(
            campaign=self.campaign,
            subscription=self.active_one,
            recipient_email=self.active_one.email,
        )
        self.active_one.status = NewsletterSubscription.Status.UNSUBSCRIBED
        self.active_one.unsubscribed_at = timezone.now()
        self.active_one.save(update_fields=["status", "unsubscribed_at", "updated_at"])

        result = send_newsletter_campaign(self.campaign.pk)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, NewsletterDelivery.Status.SKIPPED)
        self.assertEqual(result.delivered_count, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_campaign_subject_rejects_header_injection(self):
        self.campaign.subject = "Update\nBcc: attacker@example.com"
        with self.assertRaises(ValidationError):
            self.campaign.full_clean()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class NewsletterAdminTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="newsletter-admin",
            email="admin@example.com",
            password="test-password",
        )
        self.campaign = NewsletterCampaign.objects.create(subject="Monthly update", body="Hello readers.")
        NewsletterSubscription.objects.create(email="reader@example.com")

    def test_admin_send_page_requires_confirmation_then_sends(self):
        url = reverse("admin:crm_newslettercampaign_send", args=[self.campaign.pk])
        model_admin = NewsletterCampaignAdmin(NewsletterCampaign, admin.site)
        request_factory = RequestFactory()

        preview_request = request_factory.get(url)
        preview_request.user = self.admin_user
        preview = model_admin.send_view(preview_request, self.campaign.pk)
        preview.render()
        self.assertEqual(preview.status_code, 200)
        self.assertIn("Sending is irreversible", preview.content.decode())
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, NewsletterCampaign.Status.DRAFT)

        send_request = request_factory.post(url)
        send_request.user = self.admin_user
        send_request.session = {}
        send_request._messages = FallbackStorage(send_request)
        response = model_admin.send_view(send_request, self.campaign.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:crm_newslettercampaign_change", args=[self.campaign.pk]))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, NewsletterCampaign.Status.SENT)
        self.assertEqual(self.campaign.sent_by, self.admin_user)
        self.assertEqual(len(mail.outbox), 1)
