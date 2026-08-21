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
from rest_framework.test import APIClient

from apps.crm.admin import NewsletterCampaignAdmin
from apps.crm.models import (
    CrmActivity,
    FormSubmission,
    Lead,
    NewsletterCampaign,
    NewsletterDelivery,
    NewsletterSubscription,
    Opportunity,
)
from apps.crm.newsletters import (
    NewsletterEmailDeliveryNotConfigured,
    make_unsubscribe_token,
    send_newsletter_campaign,
)
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
        lead = Lead.objects.get(contact_email="jamie@example.com")
        submission = FormSubmission.objects.get(lead=lead)
        self.assertEqual(submission.form_type, FormSubmission.FormType.NEWSLETTER)
        self.assertEqual(submission.source_path, "/families/")
        self.assertNotIn("csrfmiddlewaretoken", submission.submitted_data)
        self.assertNotIn("website", submission.submitted_data)

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
    DEBUG=True,
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

    @override_settings(
        DEBUG=False,
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        PUBLIC_APP_URL="http://localhost:8000",
    )
    def test_production_send_fails_closed_without_delivery_configuration(self):
        with self.assertRaises(NewsletterEmailDeliveryNotConfigured):
            send_newsletter_campaign(self.campaign.pk)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, NewsletterCampaign.Status.DRAFT)
        self.assertFalse(self.campaign.deliveries.exists())


@override_settings(DEBUG=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
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

    @override_settings(
        DEBUG=False,
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        PUBLIC_APP_URL="http://localhost:8000",
    )
    def test_admin_confirmation_disables_send_without_production_email(self):
        url = reverse("admin:crm_newslettercampaign_send", args=[self.campaign.pk])
        model_admin = NewsletterCampaignAdmin(NewsletterCampaign, admin.site)
        request = RequestFactory().get(url)
        request.user = self.admin_user

        response = model_admin.send_view(request, self.campaign.pk)
        response.render()
        content = response.content.decode()

        self.assertIn("Email delivery is not configured", content)
        self.assertNotIn('value="Send newsletter now"', content)


class FormSubmissionIntakeTests(TestCase):
    def test_repeat_consultation_submissions_update_one_contact_and_preserve_both_events(self):
        first = {
            "name": "Jordan Reader",
            "email": "JORDAN@example.com",
            "phone": "555-0101",
            "audience": Lead.Audience.PARENT,
            "organization_name": "Family consultation",
            "estimated_students": "1",
            "child_age_grade": "Grade 2",
            "notes": "Needs help with decoding.",
            "redirect_to": "/contact/",
        }
        response = self.client.post(reverse("crm_signup"), first)
        second = {**first, "phone": "555-0199", "notes": "Following up after school meeting."}
        self.client.post(reverse("crm_signup"), second)

        self.assertRedirects(
            response,
            "/contact/?signup=thanks#consultation-form",
            fetch_redirect_response=False,
        )
        self.assertEqual(Lead.objects.count(), 1)
        lead = Lead.objects.get()
        self.assertEqual(lead.contact_email, "jordan@example.com")
        self.assertEqual(lead.contact_phone, "555-0199")
        self.assertEqual(lead.form_submissions.count(), 2)
        self.assertEqual(
            set(lead.form_submissions.values_list("form_type", flat=True)),
            {FormSubmission.FormType.CONSULTATION},
        )

    def test_assessment_follow_up_is_labeled_with_its_actual_source(self):
        self.client.post(
            reverse("crm_signup"),
            {
                "name": "Morgan Parent",
                "email": "morgan@example.com",
                "audience": Lead.Audience.PARENT,
                "organization_name": "Reading assessment follow-up",
                "notes": "Estimated reading age: 7.",
            },
        )

        submission = FormSubmission.objects.get()
        self.assertEqual(submission.form_type, FormSubmission.FormType.ASSESSMENT)
        self.assertEqual(submission.source_path, "/assessment/")

    def test_invalid_email_is_rejected_without_creating_crm_records(self):
        response = self.client.post(
            reverse("crm_signup"),
            {"name": "Bad Email", "email": "not-an-email", "redirect_to": "/contact/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(FormSubmission.objects.exists())


class CrmWorkspaceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="crm-admin",
            email="crm-admin@example.com",
            password="test-password",
        )
        self.guardian = user_model.objects.create_user(
            username="guardian",
            email="guardian@example.com",
            password="test-password",
        )
        self.lead = Lead.objects.create(
            school_name="Family inquiry",
            contact_name="Alex Reader",
            contact_email="alex@example.com",
            audience=Lead.Audience.PARENT,
        )
        FormSubmission.objects.create(
            lead=self.lead,
            form_type=FormSubmission.FormType.CONSULTATION,
            source_path="/contact/",
            submitted_data={"name": "Alex Reader", "email": "alex@example.com"},
        )

    def test_workspace_requires_central_crm_access(self):
        anonymous_response = self.client.get(reverse("crm_contact_list"))
        self.client.force_login(self.guardian)
        guardian_response = self.client.get(reverse("crm_contact_list"))

        self.assertEqual(anonymous_response.status_code, 302)
        self.assertEqual(guardian_response.status_code, 403)

    def test_leads_api_rejects_a_non_crm_portal_user(self):
        api_client = APIClient()
        api_client.force_authenticate(self.guardian)

        response = api_client.get("/api/v1/leads/")

        self.assertEqual(response.status_code, 403)

    def test_contact_index_searches_and_displays_submission_counts(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("crm_contact_list"), {"q": "alex@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alex Reader")
        self.assertContains(response, "Every valid website submission")
        self.assertEqual(list(response.context["contacts"]), [self.lead])
        self.assertEqual(response.context["contacts"][0].submission_count, 1)

    def test_recent_sort_places_captured_submissions_before_uncaptured_contacts(self):
        Lead.objects.create(
            school_name="Imported contact",
            contact_name="Recently imported",
            contact_email="imported@example.com",
            audience=Lead.Audience.OTHER,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("crm_contact_list"))

        self.assertEqual(response.context["contacts"][0].pk, self.lead.pk)

    def test_contact_properties_notes_and_tasks_are_manageable(self):
        self.client.force_login(self.admin_user)
        detail_url = reverse("crm_contact_detail", args=[self.lead.pk])
        update_response = self.client.post(
            reverse("crm_contact_update", args=[self.lead.pk]),
            {
                "status": Lead.Status.CONTACTED,
                "audience": Lead.Audience.PARENT,
                "assigned_to": self.admin_user.pk,
            },
        )
        note_response = self.client.post(
            reverse("crm_note_create", args=[self.lead.pk]),
            {"body": "Left a voicemail and sent the family guide."},
        )
        task_response = self.client.post(
            reverse("crm_task_create", args=[self.lead.pk]),
            {
                "subject": "Call Alex",
                "due_at": "2026-09-01T14:00",
                "assigned_to": self.admin_user.pk,
            },
        )

        self.assertRedirects(update_response, detail_url, fetch_redirect_response=False)
        self.assertEqual(note_response.status_code, 302)
        self.assertEqual(task_response.status_code, 302)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.CONTACTED)
        self.assertEqual(self.lead.assigned_to, self.admin_user)
        note = self.lead.crm_activities.get(activity_type=CrmActivity.ActivityType.NOTE)
        task = self.lead.crm_activities.get(activity_type=CrmActivity.ActivityType.TASK)
        self.assertEqual(note.body, "Left a voicemail and sent the family guide.")
        self.assertEqual(task.assigned_to, self.admin_user)

        complete_response = self.client.post(
            reverse("crm_task_complete", args=[self.lead.pk, task.pk])
        )
        task.refresh_from_db()
        self.assertEqual(complete_response.status_code, 302)
        self.assertIsNotNone(task.completed_at)

    def test_contact_detail_renders_unified_activity_timeline(self):
        CrmActivity.objects.create(
            lead=self.lead,
            activity_type=CrmActivity.ActivityType.NOTE,
            body="Consultation booked for Friday.",
            created_by=self.admin_user,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("crm_contact_detail", args=[self.lead.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consultation request submitted")
        self.assertContains(response, "Consultation booked for Friday.")
