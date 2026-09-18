import json
import re
from unittest.mock import MagicMock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.blog.models import BlogPost
from apps.crm.admin import NewsletterCampaignAdmin
from apps.crm.forms import DealForm
from apps.crm.models import (
    Company,
    CrmActivity,
    FormSubmission,
    IntakeTriage,
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
from apps.crm.services import normalize_relationship_interests
from apps.crm.templatetags.crm_display import crm_survey_sections
from apps.crm.views import FamilyResourcesView, NewsletterUnsubscribeView, WebsiteSignupView
from apps.users.models import AuditLog


class CrmTests(SimpleTestCase):
    def test_survey_display_groups_answered_fields_and_omits_blanks(self):
        sections = crm_survey_sections(
            {
                "name": "Margaret Suter",
                "email": "mosuter@icloud.com",
                "home_zip": "32805",
                "respondent_situation": "community_supporter",
                "supports_tried": [],
                "one_to_one_budget": "",
                "engagement_interests": ["career_interest", "general_email"],
                "survey_placement": "Main survey page",
                "blog_post_slug": "internal-source-value",
            }
        )

        self.assertEqual(
            [section["title"] for section in sections],
            ["Situation and goals", "Contact details", "Submission details"],
        )
        self.assertEqual(
            sections[0]["answers"][0]["value"],
            "Educator, specialist, local parent, donor, supporter, or other",
        )
        self.assertEqual(
            sections[0]["answers"][1]["values"],
            [
                "Educator or specialist interested in working at ClearCode",
                "Keep me on the general email list",
            ],
        )
        rendered_labels = {
            answer["label"]
            for section in sections
            for answer in section["answers"]
        }
        self.assertNotIn("Reading supports tried", rendered_labels)
        self.assertNotIn("Maximum 1-on-1 session budget", rendered_labels)
        self.assertNotIn("Blog Post Slug", rendered_labels)
        self.assertTrue(sections[0]["answers"][0]["is_wide"])

    def test_lead_pipeline_statuses_exist(self):
        self.assertIn(Lead.Status.NEW, Lead.Status.values)
        self.assertIn(Lead.Status.CONVERTED, Lead.Status.values)

    def test_five_business_pipelines_have_distinct_stage_sets(self):
        self.assertEqual(len(Opportunity.Pipeline.choices), 5)
        self.assertIn(
            Opportunity.Stage.DONOR_STEWARDSHIP,
            Opportunity.stage_values_for_pipeline(Opportunity.Pipeline.FOUNDATION_DONORS),
        )
        self.assertIn(
            Opportunity.Stage.GRANT_AWARDED,
            Opportunity.stage_values_for_pipeline(Opportunity.Pipeline.FOUNDATION_GRANTS),
        )
        self.assertNotIn(
            Opportunity.Stage.DONOR_STEWARDSHIP,
            Opportunity.stage_values_for_pipeline(Opportunity.Pipeline.FOUNDATION_GRANTS),
        )

    def test_opportunity_probability_validation(self):
        serializer = OpportunitySerializer()
        with self.assertRaisesMessage(Exception, "Probability must be between 0 and 100."):
            serializer.validate_probability(101)

    def test_relationship_interests_are_allowlisted_and_deduplicated(self):
        self.assertEqual(
            normalize_relationship_interests(
                [
                    Lead.RelationshipInterest.DONOR,
                    "unexpected",
                    Lead.RelationshipInterest.DONOR,
                    Lead.RelationshipInterest.ADVOCATE,
                ]
            ),
            [Lead.RelationshipInterest.DONOR, Lead.RelationshipInterest.ADVOCATE],
        )

    def test_website_signup_defaults_family_inquiry_for_parent(self):
        self.assertEqual(
            WebsiteSignupView._school_name_for_signup(Lead.PipelineCategory.FAMILY_ENROLLMENT, ""),
            "Family inquiry",
        )

    def test_website_signup_cleans_positive_student_count(self):
        self.assertEqual(WebsiteSignupView._clean_positive_int("24"), 24)
        self.assertIsNone(WebsiteSignupView._clean_positive_int("-1"))
        self.assertIsNone(WebsiteSignupView._clean_positive_int("many"))

    def test_homepage_positions_clear_code_as_family_first_intervention(self):
        homepage = render_to_string("index.html")

        self.assertIn("Unlock Reading.<br>Unlock Everything.", homepage)
        self.assertIn("K–8 structured literacy intervention built to close the gap", homepage)
        self.assertIn("A straightforward process, built for your child.", homepage)
        self.assertIn("Request a consultation", homepage)
        self.assertIn("Phonics for Reading", homepage)
        self.assertIn("Orton-Gillingham", homepage)
        self.assertNotIn("Homework becomes a nightly battle", homepage)
        self.assertNotIn("4x more clarity", homepage)


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
    def _resources_context(self, query=""):
        request = RequestFactory().get(f"/resources/{query}")
        request.session = self.client.session
        response = FamilyResourcesView.as_view(template_name="resources.html")(request)
        return response.context_data

    def test_family_resources_signup_unlocks_the_page_and_records_crm_intake(self):
        self.assertFalse(self._resources_context()["resources_unlocked"])

        response = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Taylor Reader",
                "email": " TAYLOR@example.com ",
                "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
                "redirect_to": "/resources/",
            },
        )

        self.assertRedirects(
            response,
            "/resources/?signup=thanks#start-here",
            fetch_redirect_response=False,
        )
        lead = Lead.objects.get(contact_email="taylor@example.com")
        self.assertEqual(lead.contact_name, "Taylor Reader")
        self.assertEqual(lead.school_name, "Family inquiry")
        self.assertTrue(lead.metadata["family_resources_access_requested"])
        self.assertEqual(lead.metadata["source_path"], "/resources/")
        submission = lead.form_submissions.get()
        self.assertEqual(submission.form_type, FormSubmission.FormType.FAMILY_RESOURCES)
        self.assertEqual(submission.get_form_type_display(), "Free resources modal")
        self.assertEqual(submission.source_path, "/resources/")
        self.assertEqual(submission.submitted_data["resource_access"], "family_resources")
        self.assertTrue(lead.came_from_free_resources_modal)
        self.assertEqual(lead.origin_labels, ["Free resources modal"])

        self.assertTrue(self._resources_context()["resources_unlocked"])

    def test_other_website_signups_are_not_marked_as_free_resources_modal(self):
        self.client.post(
            reverse("crm_signup"),
            {
                "name": "Jordan Contact",
                "email": "jordan@example.com",
                "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
                "organization_name": "Website contact",
                "notes": "Looking for tutoring options.",
                "redirect_to": "/contact/",
            },
        )

        lead = Lead.objects.get(contact_email="jordan@example.com")
        self.assertFalse(lead.came_from_free_resources_modal)
        self.assertEqual(lead.origin_labels, [])
        self.assertEqual(lead.form_submissions.get().form_type, FormSubmission.FormType.WEBSITE)

    def test_family_resources_thanks_page_shows_a_prominent_confirmation(self):
        response = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Taylor Reader",
                "email": "taylor@example.com",
                "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
                "redirect_to": "/resources/",
            },
            follow=True,
        )

        self.assertContains(response, 'data-testid="site-confirmation"')
        self.assertContains(response, "You’re in—your free family resources are ready.")
        self.assertContains(response, "site-confirmation__text")
        self.assertContains(response, "Confirmed")
        self.assertContains(response, 'role="status"')

    def test_invalid_family_resources_signup_does_not_unlock_or_create_records(self):
        response = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Taylor Reader",
                "email": "not-an-email",
                "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
                "redirect_to": "/resources/",
            },
        )

        self.assertRedirects(
            response,
            "/resources/?signup=invalid#start-here",
            fetch_redirect_response=False,
        )
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(FormSubmission.objects.exists())
        locked_context = self._resources_context("?signup=invalid")
        self.assertFalse(locked_context["resources_unlocked"])
        self.assertEqual(locked_context["resource_gate_result"], "invalid")

    def test_repeat_consultation_submissions_update_one_contact_and_preserve_both_events(self):
        first = {
            "name": "Jordan Reader",
            "email": "JORDAN@example.com",
            "phone": "555-0101",
            "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
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
            lead.opportunities.filter(pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT).count(),
            1,
        )
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
                "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
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

    def test_family_partner_checkbox_creates_triage_without_three_automatic_deals(self):
        self.client.post(
            reverse("crm_signup"),
            {
                "name": "Partner Parent",
                "email": "partner-parent@example.com",
                "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
                "organization_name": "Reading assessment follow-up",
                "partner_interest": "yes",
            },
        )

        lead = Lead.objects.get(contact_email="partner-parent@example.com")
        triage = IntakeTriage.objects.get(lead=lead)
        self.assertEqual(triage.status, IntakeTriage.Status.PENDING)
        self.assertEqual(triage.submission.submitted_data["partner_interest"], "yes")
        self.assertEqual(lead.opportunities.count(), 1)
        self.assertEqual(lead.opportunities.get().pipeline, Opportunity.Pipeline.FAMILY_ENROLLMENT)

    def test_assessment_preserves_multiple_relationship_interests_in_one_triage_item(self):
        selected_interests = [
            Lead.RelationshipInterest.REFERRAL_PARTNER,
            Lead.RelationshipInterest.DONOR,
            Lead.RelationshipInterest.ADVOCATE,
        ]

        self.client.post(
            reverse("crm_signup"),
            {
                "name": "Multi Interest Parent",
                "email": "multi-interest@example.com",
                "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
                "organization_name": "Reading assessment follow-up",
                "relationship_interests": selected_interests,
            },
        )

        lead = Lead.objects.get(contact_email="multi-interest@example.com")
        triage = IntakeTriage.objects.get(lead=lead)
        self.assertEqual(IntakeTriage.objects.filter(lead=lead).count(), 1)
        self.assertEqual(
            triage.submission.submitted_data["relationship_interests"],
            selected_interests,
        )
        self.assertEqual(lead.metadata["relationship_interests"], selected_interests)
        self.assertEqual(
            lead.relationship_interest_labels,
            ["Referral Partner", "Donor", "Advocate"],
        )
        self.assertEqual(lead.opportunities.count(), 1)
        self.assertEqual(
            lead.opportunities.get().pipeline,
            Opportunity.Pipeline.FAMILY_ENROLLMENT,
        )

    def test_assessment_follow_up_preserves_structured_results_and_updates_family_deal(self):
        response = self.client.post(
            reverse("crm_signup"),
            {
                "name": "Morgan Parent",
                "email": "morgan-structured@example.com",
                "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
                "organization_name": "Reading assessment follow-up",
                "notes": "Requested a specialist follow-up.",
                "child_name": "Avery",
                "child_age": "8",
                "home_zip": "32789",
                "child_grade": "grade_3",
                "assessment_answers": json.dumps(
                    {
                        "phonemicAwareness": 0,
                        "letterSound": 1,
                        "phonics": 2,
                        "advancedPhonics": 3,
                        "sightWords": 2,
                        "fluency": 1,
                        "vocabulary": 0,
                        "comprehension": 1,
                        "writingReadiness": 2,
                        "confidence": 1,
                    }
                ),
                "inventory_answers": json.dumps(
                    {"third-plus-01": True, "third-plus-02": False}
                ),
                "inventory_stopped_group": "0",
            },
        )

        self.assertEqual(response.status_code, 302)
        lead = Lead.objects.get(contact_email="morgan-structured@example.com")
        submission = lead.form_submissions.get()
        result = submission.submitted_data["digital_reading_result"]
        inventory = submission.submitted_data["parent_inventory_result"]
        self.assertEqual(submission.form_type, FormSubmission.FormType.ASSESSMENT)
        self.assertEqual(submission.submitted_data["child_name"], "Avery")
        self.assertEqual(result["reading_age"], 7.4)
        self.assertEqual(inventory["yes_count"], 1)
        self.assertTrue(inventory["support_recommended"])
        deal = lead.opportunities.get()
        self.assertEqual(deal.student_name, "Avery")
        self.assertEqual(deal.in_catchment_zip, "32789")
        self.assertEqual(deal.grade_band, Opportunity.GradeBand.GRADE_3_5)
        self.assertEqual(deal.metadata["latest_reading_assessment"]["child_grade"], "grade_3")


class EarlyInterestSurveyIntakeTests(TestCase):
    def _payload(self, **overrides):
        payload = {
            "source_path": "/survey/",
            "name": "Jordan Reader",
            "email": "JORDAN@example.com",
            "email_consent": "yes",
            "home_zip": "32789",
            "respondent_situation": "grade_3_5_struggling",
            "supports_tried": ["school_intervention", "specialized_tutor"],
            "annual_reading_spend": "2001_5000",
            "commitment_preference": "yes_have_time",
            "one_to_one_budget": "scholarship_esa",
            "small_group_budget": "up_to_75",
            "engagement_interests": ["priority_waitlist", "opening_updates"],
        }
        payload.update(overrides)
        return payload

    def test_all_situations_preserve_contact_and_expected_family_routing(self):
        from apps.crm.surveys import SURVEY_SITUATIONS, PARENT_AUDIENCE_SITUATIONS
        from apps.crm.models import WebsiteReceipt
        for situation in SURVEY_SITUATIONS:
            with self.subTest(situation=situation):
                self.client.post(reverse("crm_survey_submit"), self._payload(
                    email=f"{situation}@example.com", respondent_situation=situation,
                    supports_tried=[], annual_reading_spend="", commitment_preference="",
                    one_to_one_budget="", small_group_budget="", engagement_interests=["opening_updates"],
                ))
                lead = Lead.objects.get(contact_email=f"{situation}@example.com")
                self.assertEqual(lead.opportunities.exists(), situation in PARENT_AUDIENCE_SITUATIONS)
                self.assertTrue(WebsiteReceipt.objects.filter(submission__lead=lead).exists())

    def test_explicit_donor_and_referral_interests_create_both_pipelines_without_duplicates(self):
        payload = self._payload(respondent_situation="community_supporter",
            supports_tried=[], annual_reading_spend="", commitment_preference="",
            one_to_one_budget="", small_group_budget="", engagement_interests=["donor", "referral_partner"])
        for _ in range(2):
            self.client.post(reverse("crm_survey_submit"), payload)
        lead = Lead.objects.get()
        self.assertEqual(set(lead.opportunities.values_list("pipeline", flat=True)),
            {Opportunity.Pipeline.FOUNDATION_DONORS, Opportunity.Pipeline.REFERRAL_PARTNERS})
        self.assertEqual(lead.opportunities.count(), 2)
        self.assertEqual(lead.metadata["relationship_interests"], ["donor", "referral_partner"])

    def test_each_engagement_has_the_expected_destination(self):
        from apps.crm.surveys import SURVEY_ENGAGEMENTS
        for interest in SURVEY_ENGAGEMENTS:
            with self.subTest(interest=interest):
                self.client.post(reverse("crm_survey_submit"), self._payload(
                    email=f"{interest}@example.com", engagement_interests=[interest]))
                lead = Lead.objects.get(contact_email=f"{interest}@example.com")
                self.assertTrue(lead.opportunities.filter(pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT).exists())
                self.assertEqual(lead.opportunities.filter(pipeline=Opportunity.Pipeline.REFERRAL_PARTNERS).exists(),
                    interest in {"referral_partner", "refer_family", "professional_connection"})
                self.assertEqual(lead.opportunities.filter(pipeline=Opportunity.Pipeline.FOUNDATION_DONORS).exists(), interest == "donor")
                self.assertEqual(IntakeTriage.objects.filter(lead=lead, status=IntakeTriage.Status.PENDING).exists(),
                    interest in {"community_partner", "career_interest"})

    def test_career_interest_alone_is_actionable_in_triage(self):
        self.client.post(reverse("crm_survey_submit"), self._payload(
            respondent_situation="community_supporter", supports_tried=[], annual_reading_spend="",
            commitment_preference="", one_to_one_budget="", small_group_budget="",
            engagement_interests=["career_interest"]))
        self.assertEqual(IntakeTriage.objects.get().status, IntakeTriage.Status.PENDING)
        self.assertTrue(Lead.objects.get().metadata["career_interest"])

    def test_success_replaces_form_and_survives_refresh(self):
        response = self.client.post(reverse("crm_survey_submit"), self._payload(), follow=True)
        self.assertContains(response, "Thank you!")
        self.assertNotContains(response, 'data-survey-form novalidate')
        self.assertContains(self.client.get(response.redirect_chain[-1][0]), "Thank you!")

    def test_success_url_alone_does_not_claim_a_saved_submission(self):
        self.assertNotContains(self.client.get("/survey/?survey=thanks"), "<h2>Thank you!</h2>")

    def test_main_survey_preserves_answers_and_routes_family_properties(self):
        response = self.client.post(reverse("crm_survey_submit"), self._payload())

        self.assertRedirects(
            response,
            "/survey/?survey=thanks#early-interest-survey",
            fetch_redirect_response=False,
        )
        lead = Lead.objects.get(contact_email="jordan@example.com")
        submission = lead.form_submissions.get()
        self.assertEqual(submission.form_type, FormSubmission.FormType.SURVEY)
        self.assertEqual(submission.source_path, "/survey/")
        self.assertEqual(
            submission.submitted_data["supports_tried"],
            ["school_intervention", "specialized_tutor"],
        )
        self.assertEqual(submission.submitted_data["survey_placement"], "Main survey page")
        self.assertEqual(lead.metadata["home_zip"], "32789")
        self.assertEqual(
            lead.metadata["engagement_interests"],
            ["priority_waitlist", "opening_updates"],
        )
        deal = lead.opportunities.get()
        self.assertEqual(deal.pipeline, Opportunity.Pipeline.FAMILY_ENROLLMENT)
        self.assertEqual(deal.stage, Opportunity.Stage.FAMILY_WAITLIST)
        self.assertEqual(deal.grade_band, Opportunity.GradeBand.GRADE_3_5)
        self.assertEqual(deal.in_catchment_zip, "32789")
        self.assertEqual(deal.funding_type, Opportunity.FundingType.ESA)
        subscription = NewsletterSubscription.objects.get(email="jordan@example.com")
        self.assertEqual(subscription.source_path, "/survey/")
        self.assertEqual(subscription.consent_version, "survey-opening-updates-v2")
        self.assertIsNotNone(subscription.consented_at)

    def test_blog_survey_deduplicates_contact_and_preserves_article_attribution(self):
        post = BlogPost.objects.create(
            title="A clear reading path",
            excerpt="Practical next steps.",
            body="Article body.",
            status=BlogPost.Status.PUBLISHED,
        )
        self.client.post(reverse("crm_survey_submit"), self._payload())

        response = self.client.post(
            reverse("crm_survey_submit"),
            self._payload(
                source_path=post.get_absolute_url(),
                blog_post_slug=post.slug,
                engagement_interests=["consultation"],
            ),
        )

        self.assertRedirects(
            response,
            f"{post.get_absolute_url()}?survey=thanks#early-interest-survey",
            fetch_redirect_response=False,
        )
        self.assertEqual(Lead.objects.count(), 1)
        lead = Lead.objects.get()
        self.assertEqual(lead.form_submissions.count(), 2)
        blog_submission = lead.form_submissions.get(source_path=post.get_absolute_url())
        self.assertEqual(blog_submission.submitted_data["survey_placement"], "Blog article")
        self.assertEqual(blog_submission.submitted_data["blog_post_title"], post.title)
        self.assertEqual(blog_submission.submitted_data["blog_post_slug"], post.slug)

    def test_non_parent_partner_signal_enters_triage_without_family_deal(self):
        response = self.client.post(
            reverse("crm_survey_submit"),
            self._payload(
                respondent_situation="community_supporter",
                supports_tried=[],
                annual_reading_spend="",
                commitment_preference="",
                one_to_one_budget="",
                small_group_budget="",
                engagement_interests=["community_partner", "career_interest"],
            ),
        )

        self.assertEqual(response.status_code, 302)
        lead = Lead.objects.get()
        self.assertEqual(lead.audience, Lead.PipelineCategory.OTHER)
        self.assertFalse(lead.opportunities.exists())
        triage = IntakeTriage.objects.get(lead=lead)
        self.assertEqual(triage.source_signal, IntakeTriage.SourceSignal.PARTNER_INTEREST)
        self.assertEqual(
            triage.submission.submitted_data["engagement_interests"],
            ["community_partner", "career_interest"],
        )

    def test_survey_rejects_conditional_tampering(self):
        tampered = self.client.post(
            reverse("crm_survey_submit"),
            self._payload(
                email="other@example.com",
                respondent_situation="community_supporter",
                engagement_interests=["priority_waitlist"],
            ),
        )

        self.assertEqual(tampered.status_code, 302)
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(FormSubmission.objects.exists())

    def test_survey_without_checkbox_saves_without_newsletter_opt_in(self):
        payload = self._payload(engagement_interests=["consultation"])
        payload.pop("email_consent")
        self.client.post(reverse("crm_survey_submit"), payload)
        self.assertEqual(FormSubmission.objects.get().submitted_data["email_consent"], "no")
        self.assertFalse(NewsletterSubscription.objects.exists())

    def test_community_opening_updates_is_visible_in_pending_triage(self):
        self.client.post(reverse("crm_survey_submit"), self._payload(
            email_consent="", respondent_situation="community_supporter",
            supports_tried=[], annual_reading_spend="", commitment_preference="",
            one_to_one_budget="", small_group_budget="", engagement_interests=["opening_updates"],
        ))
        lead = Lead.objects.get()
        self.assertEqual(IntakeTriage.objects.get(lead=lead).status, IntakeTriage.Status.PENDING)
        self.assertFalse(lead.opportunities.exists())
        self.assertEqual(lead.form_submissions.count(), 1)
        self.assertTrue(NewsletterSubscription.objects.filter(email=lead.contact_email).exists())

    def test_all_new_commitment_choices_are_saved_and_legacy_labels_remain_readable(self):
        from apps.crm.surveys import SURVEY_COMMITMENTS, SURVEY_VALUE_LABELS
        for choice in SURVEY_COMMITMENTS:
            with self.subTest(choice=choice):
                self.client.post(reverse("crm_survey_submit"), self._payload(
                    email=f"{choice}@example.com", commitment_preference=choice))
                submission = FormSubmission.objects.get(lead__contact_email=f"{choice}@example.com")
                self.assertEqual(submission.submitted_data["commitment_preference"], choice)
        self.assertEqual(SURVEY_VALUE_LABELS["weekly_few_months"], "One session per week for a few months")

    def test_survey_honeypot_is_discarded_without_records(self):
        response = self.client.post(
            reverse("crm_survey_submit"),
            {"source_path": "/survey/", "website": "https://spam.example"},
        )

        self.assertRedirects(
            response,
            "/survey/?survey=thanks#early-interest-survey",
            fetch_redirect_response=False,
        )
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
            audience=Lead.PipelineCategory.FAMILY_ENROLLMENT,
        )
        FormSubmission.objects.create(
            lead=self.lead,
            form_type=FormSubmission.FormType.CONSULTATION,
            source_path="/contact/",
            submitted_data={"name": "Alex Reader", "email": "alex@example.com"},
        )

    def test_contact_dropdown_shows_all_existing_deals_and_excludes_deleted(self):
        enrollment = Opportunity.objects.create(
            lead=self.lead, student_name="Avery Reader", term_year="2026–2027",
        )
        gift = Opportunity.objects.create(
            lead=self.lead, pipeline=Opportunity.Pipeline.FOUNDATION_DONORS,
            stage=Opportunity.initial_stage_for_pipeline(Opportunity.Pipeline.FOUNDATION_DONORS),
            campaign_year="Annual fund 2026",
        )
        Opportunity.objects.create(lead=self.lead, name="Deleted opportunity", is_deleted=True)
        deleted_contact = Lead.objects.create(contact_name="Deleted contact", is_deleted=True)
        form = DealForm(initial={"lead": self.lead.pk})
        Lead.objects.bulk_create([Lead(contact_name=f"Other contact {index}") for index in range(5)])
        with CaptureQueriesContext(connection) as queries:
            markup = form["lead"].as_widget()
        selects = [query for query in queries if query["sql"].startswith("SELECT")]
        self.assertEqual(len(selects), 2)
        self.assertIn(enrollment.name, markup)
        self.assertIn(gift.name, markup)
        self.assertIn(enrollment.get_pipeline_display(), markup)
        self.assertIn(gift.get_stage_display(), markup)
        self.assertIn('data-deals=', markup)
        self.assertIn(f'value="{self.lead.pk}" selected', markup)
        self.assertNotIn("Deleted opportunity", markup)
        self.assertNotIn(deleted_contact.contact_name, markup)

    def test_contact_dropdown_empty_state_edit_and_bound_selection(self):
        empty_contact = Lead.objects.create(contact_name="New contact")
        deal = Opportunity.objects.create(lead=self.lead, student_name="Avery", term_year="2026")
        edit_form = DealForm(instance=deal)
        self.assertIn(deal.name, edit_form["lead"].as_widget())
        bound_form = DealForm(data={"lead": str(empty_contact.pk)})
        markup = bound_form["lead"].as_widget()
        self.assertIn("No existing deals", markup)
        self.assertIn(f'value="{empty_contact.pk}" selected', markup)
        self.assertIn('data-deals="[]"', markup)

    def test_contact_deal_context_escapes_untrusted_names(self):
        deal = Opportunity.objects.create(lead=self.lead, name='<script>alert("x")</script>')
        markup = DealForm()["lead"].as_widget()
        self.assertNotIn(deal.name, markup)
        self.assertIn("&lt;script&gt;", markup)
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("crm_deal_new"))
        self.assertContains(response, 'id="contact-deals"')
        self.assertContains(response, "aria-live=\"polite\"")

    def test_deal_company_can_be_typed_instead_of_selected(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("crm_deal_new"),
            {
                "lead": "",
                "company": "",
                "company_name": " Pine Foundation ",
                "pipeline": Opportunity.Pipeline.FOUNDATION_GRANTS,
                "stage": Opportunity.initial_stage_for_pipeline(Opportunity.Pipeline.FOUNDATION_GRANTS),
                "program_name": "Literacy fund",
                "cycle_year": "2026",
                "value": "25000",
            },
        )

        company = Company.objects.get(name="Pine Foundation")
        deal = Opportunity.objects.get(company=company)
        self.assertRedirects(response, reverse("crm_deal_detail", args=[deal.pk]))
        self.assertEqual(company.owner, self.admin_user)
        self.assertEqual(deal.name, "Pine Foundation — Literacy fund — 2026")

    def test_typed_deal_company_reuses_an_existing_name_and_rejects_both(self):
        self.client.force_login(self.admin_user)
        existing = Company.objects.create(name="Pine Foundation")

        reused = self.client.post(
            reverse("crm_deal_new"),
            {
                "lead": "",
                "company": "",
                "company_name": "pine foundation",
                "pipeline": Opportunity.Pipeline.EQUITY_INVESTMENT,
                "stage": Opportunity.initial_stage_for_pipeline(Opportunity.Pipeline.EQUITY_INVESTMENT),
                "investment_round": "Seed",
                "value": "0",
            },
        )
        both = self.client.post(
            reverse("crm_deal_new"),
            {
                "lead": "",
                "company": str(existing.pk),
                "company_name": "Second company",
                "pipeline": Opportunity.Pipeline.EQUITY_INVESTMENT,
                "stage": Opportunity.initial_stage_for_pipeline(Opportunity.Pipeline.EQUITY_INVESTMENT),
                "investment_round": "Series A",
                "value": "0",
            },
        )

        self.assertEqual(reused.status_code, 302)
        self.assertEqual(Company.objects.filter(name__iexact="pine foundation").count(), 1)
        self.assertEqual(Opportunity.objects.get(investment_round="Seed").company, existing)
        self.assertEqual(both.status_code, 400)
        self.assertContains(both, "Choose an existing company or create a new one, not both.", status_code=400)
        self.assertFalse(Company.objects.filter(name="Second company").exists())

    def test_typed_deal_company_is_not_created_when_the_deal_fails_to_save(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("crm_deal_new"),
            {
                "lead": "",
                "company": "",
                "company_name": "Abandoned Foundation",
                "pipeline": Opportunity.Pipeline.FOUNDATION_GRANTS,
                "stage": Opportunity.initial_stage_for_pipeline(Opportunity.Pipeline.FOUNDATION_GRANTS),
                "program_name": "",
                "cycle_year": "",
                "value": "0",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Opportunity.objects.filter(is_deleted=False).exists())
        self.assertFalse(Company.objects.filter(name="Abandoned Foundation").exists())

    def test_deal_form_offers_a_company_text_field(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("crm_deal_new"))

        self.assertContains(response, 'name="company_name"')
        self.assertContains(response, "Or create a company")

    def test_workspace_requires_central_crm_access(self):
        anonymous_response = self.client.get(reverse("crm_dashboard"))
        self.client.force_login(self.guardian)
        guardian_response = self.client.get(reverse("crm_dashboard"))

        self.assertEqual(anonymous_response.status_code, 302)
        self.assertEqual(guardian_response.status_code, 403)

    def test_admin_dashboard_header_exposes_crm_in_business_menu(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("portal_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="business-menu-button"')
        self.assertContains(response, 'data-testid="crm-header-link"')
        self.assertContains(response, 'aria-label="Workspace sections"')
        self.assertContains(response, f'href="{reverse("crm_dashboard")}"')

    def test_crm_opens_on_an_actionable_overview(self):
        CrmActivity.objects.create(
            lead=self.lead,
            activity_type=CrmActivity.ActivityType.TASK,
            subject="Call Alex",
            due_at=timezone.now() - timezone.timedelta(hours=1),
            assigned_to=self.admin_user,
            created_by=self.admin_user,
        )
        Opportunity.objects.create(
            lead=self.lead,
            owner=self.admin_user,
            name="Family enrollment",
            pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
            stage=Opportunity.Stage.FAMILY_LEAD_NURTURE,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("crm_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What needs attention")
        self.assertContains(response, "Call Alex")
        self.assertContains(response, "Overdue")
        self.assertContains(response, "Families / Enrollment")
        self.assertContains(response, "Simple CRM flow")

    def test_contact_is_created_inside_the_crm_without_admin_fields(self):
        self.client.force_login(self.admin_user)

        get_response = self.client.get(reverse("crm_contact_create"))
        response = self.client.post(
            reverse("crm_contact_create"),
            {
                "contact_name": "Jordan Teacher",
                "contact_email": " JORDAN@EXAMPLE.COM ",
                "contact_phone": "555-0100",
                "audience": Lead.PipelineCategory.REFERRAL_PARTNERS,
                "company_name": "Pine School",
                "source": Lead.Source.REFERRAL,
                "status": Lead.Status.NEW,
                "assigned_to": self.admin_user.pk,
                "estimated_students": 12,
                "notes": "Met at a literacy event.",
            },
        )

        contact = Lead.objects.get(contact_email="jordan@example.com")
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Add a contact")
        self.assertNotContains(get_response, "Is deleted")
        self.assertNotContains(get_response, "/admin/crm/lead/add/")
        self.assertRedirects(response, reverse("crm_contact_detail", args=[contact.pk]))
        self.assertEqual(contact.company.name, "Pine School")
        self.assertEqual(contact.organization_name, "Pine School")
        self.assertEqual(contact.assigned_to, self.admin_user)
        self.assertEqual(contact.crm_activities.get().body, "Met at a literacy event.")

    def test_contact_creation_opens_an_existing_email_instead_of_duplicating(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("crm_contact_create"),
            {
                "contact_name": "Alex Duplicate",
                "contact_email": "ALEX@example.com",
                "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
                "source": Lead.Source.OTHER,
                "status": Lead.Status.NEW,
            },
        )

        self.assertRedirects(response, reverse("crm_contact_detail", args=[self.lead.pk]))
        self.assertEqual(Lead.objects.filter(contact_email__iexact="alex@example.com").count(), 1)

    def test_contacts_from_the_free_resources_modal_are_visibly_marked(self):
        self.client.post(
            reverse("crm_signup"),
            {
                "name": "Taylor Reader",
                "email": "taylor@example.com",
                "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
                "redirect_to": "/resources/",
            },
        )
        lead = Lead.objects.get(contact_email="taylor@example.com")
        self.client.force_login(self.admin_user)

        contact_list = self.client.get(reverse("crm_contact_list"))
        contact_detail = self.client.get(reverse("crm_contact_detail", args=[lead.pk]))

        self.assertContains(
            contact_list,
            '<span class="badge badge-origin" data-testid="contact-origin">Free resources modal</span>',
        )
        self.assertContains(
            contact_detail,
            '<span class="badge badge-origin" data-testid="contact-origin">Free resources modal</span>',
        )
        self.assertContains(contact_detail, "Submitted the free resources modal on /resources/")
        self.assertContains(contact_detail, "Free resources modal submitted")

    def test_contacts_from_other_forms_carry_no_free_resources_badge(self):
        self.client.force_login(self.admin_user)

        contact_list = self.client.get(reverse("crm_contact_list"))
        contact_detail = self.client.get(reverse("crm_contact_detail", args=[self.lead.pk]))

        self.assertNotContains(contact_list, 'data-testid="contact-origin"')
        self.assertNotContains(contact_detail, 'data-testid="contact-origin"')

    def test_generic_admin_cannot_create_leads(self):
        self.client.force_login(self.admin_user)

        contact_list = self.client.get(reverse("crm_contact_list"))
        admin_add = self.client.get(reverse("admin:crm_lead_add"))

        self.assertNotContains(contact_list, "/admin/crm/lead/add/")
        self.assertEqual(admin_add.status_code, 403)

    def test_company_create_and_edit_stay_inside_the_crm(self):
        self.client.force_login(self.admin_user)

        create_response = self.client.post(
            reverse("crm_company_create"),
            {
                "name": "North Star Learning",
                "website": "https://northstar.example.com",
                "owner": self.admin_user.pk,
                "notes": "School partnership prospect.",
            },
        )
        company = Company.objects.get(name="North Star Learning")
        detail_response = self.client.get(reverse("crm_company_detail", args=[company.pk]))
        update_response = self.client.post(
            reverse("crm_company_update", args=[company.pk]),
            {
                "name": "North Star Learning Center",
                "website": "https://northstar.example.com",
                "owner": self.admin_user.pk,
                "notes": "Qualified school partnership prospect.",
            },
        )

        self.assertRedirects(create_response, reverse("crm_company_detail", args=[company.pk]))
        self.assertContains(detail_response, reverse("crm_company_update", args=[company.pk]))
        self.assertNotContains(detail_response, f"/admin/crm/company/{company.pk}/change/")
        self.assertRedirects(update_response, reverse("crm_company_detail", args=[company.pk]))
        company.refresh_from_db()
        self.assertEqual(company.name, "North Star Learning Center")

    def test_dashboard_header_highlights_dashboard_instead_of_crm(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("portal_dashboard"))

        self.assertEqual(response.status_code, 200)
        dashboard_link = re.search(
            r'<a\s+[^>]*data-testid="dashboard-header-link"[^>]*>',
            response.content.decode(),
        )
        crm_link = re.search(
            r'<a\s+[^>]*data-testid="crm-header-link"[^>]*>',
            response.content.decode(),
        )
        self.assertIsNotNone(dashboard_link)
        self.assertIsNotNone(crm_link)
        self.assertIn('aria-current="page"', dashboard_link.group())
        self.assertNotIn('aria-current="page"', crm_link.group())
        self.assertContains(response, "/assets/logo/cc-monogram-gold-teal.png")

    def test_inbox_header_highlights_inbox_instead_of_dashboard(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("portal_inbox"))

        self.assertEqual(response.status_code, 200)
        dashboard_link = re.search(
            r'<a\s+[^>]*data-testid="dashboard-header-link"[^>]*>',
            response.content.decode(),
        )
        inbox_link = re.search(
            r'<a\s+[^>]*data-testid="inbox-header-link"[^>]*>',
            response.content.decode(),
        )
        self.assertIsNotNone(dashboard_link)
        self.assertIsNotNone(inbox_link)
        self.assertNotIn('aria-current="page"', dashboard_link.group())
        self.assertIn('aria-current="page"', inbox_link.group())
        self.assertContains(response, "Fluency first")
        self.assertContains(response, 'aria-live="polite"')

    def test_crm_workspaces_include_phone_specific_navigation_and_views(self):
        self.client.force_login(self.admin_user)

        contacts = self.client.get(reverse("crm_contact_list"))
        companies = self.client.get(reverse("crm_company_list"))
        deals = self.client.get(reverse("crm_deal_list"))

        self.assertContains(contacts, 'class="mobile-nav"')
        self.assertContains(contacts, 'class="contact-cards"')
        self.assertContains(contacts, "font-size:16px")
        self.assertContains(companies, ".page-head > div,.search")
        self.assertContains(deals, 'class="mobile-pipeline-picker"')
        self.assertContains(deals, "grid-auto-flow:row")

    def test_non_staff_school_admin_is_not_available_as_a_crm_owner(self):
        user_model = get_user_model()
        school_admin = user_model.objects.create_user(
            username="school-admin",
            email="school-admin@example.com",
            password="test-password",
            role=user_model.Role.SCHOOL_ADMIN,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("crm_contact_detail", args=[self.lead.pk]))

        self.assertNotIn(school_admin, response.context["owners"])

    def test_super_admin_creates_a_least_privilege_crm_user(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("crm_team"),
            {
                "first_name": "Casey",
                "last_name": "Coordinator",
                "email": " CASEY@EXAMPLE.COM ",
                "password1": "",
                "password2": "",
            },
            follow=True,
        )

        team_member = get_user_model().objects.get(email="casey@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(team_member.has_usable_password())
        self.assertTrue(hasattr(team_member, "invitation"))
        self.assertNotContains(response, "Temporary password:")
        self.assertEqual(team_member.role, get_user_model().Role.CRM_USER)
        self.assertTrue(team_member.is_active)
        self.assertFalse(team_member.is_staff)
        self.assertFalse(team_member.is_superuser)
        self.assertTrue(team_member.has_crm_access)
        self.assertTrue(
            AuditLog.objects.filter(
                action="crm.team_member.created",
                actor=self.admin_user,
                entity_id=str(team_member.pk),
            ).exists()
        )

    def test_crm_user_can_work_leads_without_creating_more_users(self):
        crm_user = get_user_model().objects.create_user(
            username="crm-coordinator",
            email="coordinator@example.com",
            password="test-password",
            role=get_user_model().Role.CRM_USER,
        )
        self.client.force_login(crm_user)

        dashboard = self.client.get(reverse("crm_dashboard"))
        team = self.client.get(reverse("crm_team"))
        portal = self.client.get(reverse("portal_dashboard"))
        create_user = self.client.post(
            reverse("crm_team"),
            {
                "first_name": "No",
                "email": "not-allowed@example.com",
            },
        )
        api_client = APIClient()
        api_client.force_authenticate(crm_user)
        api_response = api_client.get("/api/v1/leads/")

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(team.status_code, 200)
        self.assertNotContains(team, "Add a backend employee")
        self.assertEqual(portal.status_code, 302)
        self.assertEqual(portal.url, reverse("crm_dashboard"))
        self.assertEqual(create_user.status_code, 403)
        self.assertEqual(api_response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(email="not-allowed@example.com").exists())

    def test_crm_user_creation_rejects_duplicate_email_and_ignores_supplied_password(self):
        self.client.force_login(self.admin_user)

        duplicate = self.client.post(
            reverse("crm_team"),
            {
                "first_name": "Duplicate",
                "email": " CRM-ADMIN@EXAMPLE.COM ",
                "password1": "Strong-Crm-Password-482!",
                "password2": "Strong-Crm-Password-482!",
            },
        )
        weak = self.client.post(
            reverse("crm_team"),
            {
                "first_name": "Weak",
                "email": "weak@example.com",
                "password1": "password",
                "password2": "password",
            },
        )

        self.assertEqual(duplicate.status_code, 400)
        self.assertContains(duplicate, "A user with this email already exists.", status_code=400)
        self.assertEqual(weak.status_code, 302)
        self.assertFalse(get_user_model().objects.get(email="weak@example.com").has_usable_password())

    def test_selected_contacts_can_be_bulk_assigned_to_a_crm_user(self):
        crm_user = get_user_model().objects.create_user(
            username="lead-owner",
            email="lead-owner@example.com",
            password="test-password",
            first_name="Lead",
            last_name="Owner",
            role=get_user_model().Role.CRM_USER,
        )
        selected = Lead.objects.create(
            school_name="Second inquiry",
            contact_name="Bailey Reader",
            contact_email="bailey@example.com",
            audience=Lead.PipelineCategory.FAMILY_ENROLLMENT,
        )
        untouched = Lead.objects.create(
            school_name="Third inquiry",
            contact_name="Cameron Reader",
            contact_email="cameron@example.com",
            audience=Lead.PipelineCategory.FAMILY_ENROLLMENT,
        )
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("crm_contact_bulk_assign"),
            {
                "lead_ids": [self.lead.pk, selected.pk],
                "assigned_to": crm_user.pk,
            },
        )

        self.assertRedirects(response, reverse("crm_contact_list"), fetch_redirect_response=False)
        self.lead.refresh_from_db()
        selected.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual(self.lead.assigned_to, crm_user)
        self.assertEqual(selected.assigned_to, crm_user)
        self.assertIsNone(untouched.assigned_to)
        assignment_logs = AuditLog.objects.filter(
            action="crm.contact.owner_updated",
            metadata__source="bulk_assignment",
        )
        self.assertEqual(assignment_logs.count(), 2)
        self.assertSetEqual(
            set(assignment_logs.values_list("entity_id", flat=True)),
            {str(self.lead.pk), str(selected.pk)},
        )

    def test_bulk_assignment_rejects_an_ineligible_owner_without_changes(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("crm_contact_bulk_assign"),
            {"lead_ids": [self.lead.pk], "assigned_to": self.guardian.pk},
        )

        self.assertRedirects(response, reverse("crm_contact_list"), fetch_redirect_response=False)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.assigned_to)
        self.assertFalse(
            AuditLog.objects.filter(action="crm.contact.owner_updated", entity_id=str(self.lead.pk)).exists()
        )

    def test_contacts_and_team_pages_expose_assignment_workflow(self):
        self.client.force_login(self.admin_user)

        contacts = self.client.get(reverse("crm_contact_list"))
        team = self.client.get(reverse("crm_team"))

        self.assertContains(contacts, reverse("crm_contact_bulk_assign"))
        self.assertContains(contacts, "Assign selected contacts to")
        self.assertContains(contacts, f"?owner={self.admin_user.pk}")
        self.assertContains(contacts, "My contacts")
        self.assertContains(contacts, reverse("crm_team"))
        self.assertContains(team, "Assignment-ready users")
        self.assertContains(team, "Add a backend employee")

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
            audience=Lead.PipelineCategory.OTHER,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("crm_contact_list"))

        self.assertEqual(response.context["contacts"][0].pk, self.lead.pk)

    def test_contact_index_shows_linked_deals_and_sorts_by_deal_name(self):
        Opportunity.objects.create(lead=self.lead, name="Zeta enrollment")
        Opportunity.objects.create(lead=self.lead, name="Hidden deal", is_deleted=True)
        alpha_contact = Lead.objects.create(
            school_name="Alpha school",
            contact_name="Blake Alpha",
            contact_email="blake@example.com",
            audience=Lead.PipelineCategory.OTHER,
        )
        Opportunity.objects.create(lead=alpha_contact, name="Alpha partnership")
        no_deal_contact = Lead.objects.create(
            school_name="No deal",
            contact_name="Casey Nodeal",
            contact_email="casey@example.com",
            audience=Lead.PipelineCategory.OTHER,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("crm_contact_list"), {"sort": "deal"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zeta enrollment")
        self.assertContains(response, "Alpha partnership")
        self.assertNotContains(response, "Hidden deal")
        self.assertEqual(
            [contact.pk for contact in response.context["contacts"]],
            [alpha_contact.pk, self.lead.pk, no_deal_contact.pk],
        )

        descending = self.client.get(reverse("crm_contact_list"), {"sort": "deal_desc"})
        self.assertEqual(
            [contact.pk for contact in descending.context["contacts"]],
            [self.lead.pk, alpha_contact.pk, no_deal_contact.pk],
        )

    def test_contact_index_filters_each_relationship_interest_separately(self):
        donor = Lead.objects.create(
            school_name="Donor inquiry",
            contact_name="Dana Donor",
            contact_email="donor@example.com",
            audience=Lead.PipelineCategory.OTHER,
            metadata={
                "relationship_interests": [
                    Lead.RelationshipInterest.REFERRAL_PARTNER,
                    Lead.RelationshipInterest.DONOR,
                ]
            },
        )
        Lead.objects.create(
            school_name="Advocate inquiry",
            contact_name="Avery Advocate",
            contact_email="advocate@example.com",
            audience=Lead.PipelineCategory.OTHER,
            metadata={"relationship_interests": [Lead.RelationshipInterest.ADVOCATE]},
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("crm_contact_list"),
            {"relationship_interest": Lead.RelationshipInterest.DONOR},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["contacts"]), [donor])
        self.assertContains(response, "Dana Donor")
        self.assertNotContains(response, "Avery Advocate")
        self.assertEqual(
            response.context["active_filters"]["relationship_interest"],
            Lead.RelationshipInterest.DONOR,
        )

    def test_inline_contact_properties_preserve_other_fields(self):
        self.client.force_login(self.admin_user)
        company = Company.objects.create(name="Reading school")
        self.lead.company = company
        self.lead.assigned_to = self.admin_user
        self.lead.save()
        url = reverse("crm_contact_update", args=[self.lead.pk])
        for field, value in (("status", Lead.Status.CONTACTED), ("audience", Lead.PipelineCategory.FAMILY_ENROLLMENT), ("assigned_to", ""), ("company", "")):
            self.lead.refresh_from_db()
            before = {name: getattr(self.lead, name) for name in ("status", "audience", "assigned_to_id", "company_id")}
            response = self.client.post(url, {"field": field, field: value, "company_name": "Must not create"})
            self.assertEqual(response.status_code, 302)
            self.lead.refresh_from_db()
            model_field = field + "_id" if field in ("company", "assigned_to") else field
            self.assertEqual(getattr(self.lead, model_field), value or None)
            for name, previous in before.items():
                if name != model_field:
                    self.assertEqual(getattr(self.lead, name), previous)
        self.assertFalse(Company.objects.filter(name="Must not create").exists())

    def test_inline_contact_rejects_invalid_fields_and_values(self):
        self.client.force_login(self.admin_user)
        url = reverse("crm_contact_update", args=[self.lead.pk])
        initial_status = self.lead.status
        for data in ({"field": "contact_email", "contact_email": "bad@example.com"}, {"field": "status"}, {"field": "status", "status": "invalid"}, {"field": "assigned_to", "assigned_to": "invalid"}, {"field": "company", "company": "999999999"}):
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 302)
            self.lead.refresh_from_db()
            self.assertEqual(self.lead.status, initial_status)
            self.assertIsNone(self.lead.company_id)

    def test_contact_detail_has_inline_and_sidebar_editors(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("crm_contact_detail", args=[self.lead.pk]))
        for field in ("status", "audience", "company", "assigned_to"):
            self.assertContains(response, f'id="inline-{field}"')
            self.assertContains(response, f'name="field" value="{field}"')
        self.assertContains(response, 'id="detail-status"')
        self.assertContains(response, "Save properties")

    def test_contact_properties_notes_and_tasks_are_manageable(self):
        self.client.force_login(self.admin_user)
        detail_url = reverse("crm_contact_detail", args=[self.lead.pk])
        update_response = self.client.post(
            reverse("crm_contact_update", args=[self.lead.pk]),
            {
                "status": Lead.Status.CONTACTED,
                "audience": Lead.PipelineCategory.FAMILY_ENROLLMENT,
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

    def test_contact_detail_renders_scannable_survey_sections(self):
        FormSubmission.objects.create(
            lead=self.lead,
            form_type=FormSubmission.FormType.SURVEY,
            source_path="/survey/",
            submitted_data={
                "name": "Alex Reader",
                "email": "alex@example.com",
                "home_zip": "32805",
                "respondent_situation": "community_supporter",
                "supports_tried": [],
                "annual_reading_spend": "",
                "commitment_preference": "",
                "one_to_one_budget": "",
                "small_group_budget": "",
                "engagement_interests": ["career_interest", "general_email"],
                "email_consent": "yes",
                "survey_placement": "Main survey page",
            },
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("crm_contact_detail", args=[self.lead.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="survey-response"')
        self.assertContains(response, "Situation and goals")
        self.assertContains(response, "Contact details")
        self.assertContains(
            response,
            "Educator or specialist interested in working at ClearCode",
        )
        self.assertContains(response, "Keep me on the general email list")
        self.assertNotContains(response, "Maximum 1-on-1 session budget")
        self.assertNotContains(response, "Reading support spend in the last 12 months")

    def test_one_company_can_hold_linked_grant_and_equity_deals(self):
        company = Company.objects.create(name="North Star Foundation", owner=self.admin_user)
        self.lead.company = company
        self.lead.save(update_fields=["company", "updated_at"])
        triage = IntakeTriage.objects.create(
            lead=self.lead,
            submission=self.lead.form_submissions.get(),
            source_signal=IntakeTriage.SourceSignal.PARTNER_INTEREST,
        )
        self.client.force_login(self.admin_user)

        triage_response = self.client.get(reverse("crm_triage_list"))

        response = self.client.post(
            reverse("crm_triage_resolve", args=[triage.pk]),
            {
                "pipelines": [
                    Opportunity.Pipeline.FOUNDATION_GRANTS,
                    Opportunity.Pipeline.EQUITY_INVESTMENT,
                ],
                "resolution_notes": "Institution is exploring both structures.",
                "action": "resolve",
            },
        )

        self.assertEqual(triage_response.status_code, 200)
        self.assertContains(triage_response, "North Star Foundation")
        self.assertRedirects(response, reverse("crm_triage_list"), fetch_redirect_response=False)
        triage.refresh_from_db()
        deals = list(company.deals.order_by("pipeline"))
        self.assertEqual(triage.status, IntakeTriage.Status.RESOLVED)
        self.assertEqual(len(deals), 2)
        self.assertEqual({deal.pipeline for deal in deals}, {
            Opportunity.Pipeline.FOUNDATION_GRANTS,
            Opportunity.Pipeline.EQUITY_INVESTMENT,
        })
        self.assertEqual(deals[0].related_deals.get(), deals[1])

    def test_partner_interest_can_route_to_advocate_without_creating_a_pipeline(self):
        triage = IntakeTriage.objects.create(
            lead=self.lead,
            submission=self.lead.form_submissions.get(),
            source_signal=IntakeTriage.SourceSignal.PARTNER_INTEREST,
        )
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("crm_triage_resolve", args=[triage.pk]),
            {
                "advocate": "yes",
                "resolution_notes": "Community advocate; no active deal process.",
                "action": "resolve",
            },
        )

        triage.refresh_from_db()
        self.assertRedirects(response, reverse("crm_triage_list"), fetch_redirect_response=False)
        self.assertEqual(triage.status, IntakeTriage.Status.RESOLVED)
        self.assertTrue(triage.advocate_selected)
        self.assertEqual(triage.selected_pipelines, [])
        self.assertFalse(Opportunity.objects.exists())

    def test_pipeline_board_uses_only_stages_for_selected_pipeline(self):
        Opportunity.objects.create(
            lead=self.lead,
            owner=self.admin_user,
            name="Family enrollment",
            pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
            stage=Opportunity.Stage.FAMILY_LEAD_NURTURE,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("crm_deal_list"),
            {"pipeline": Opportunity.Pipeline.FOUNDATION_DONORS},
        )

        labels = [column["label"] for column in response.context["stage_columns"]]
        self.assertEqual(response.status_code, 200)
        self.assertIn("Stewardship", labels)
        self.assertNotIn("Assessment", labels)

    def test_deal_rejects_a_stage_from_another_pipeline(self):
        deal = Opportunity(
            lead=self.lead,
            name="Donor relationship",
            pipeline=Opportunity.Pipeline.FOUNDATION_DONORS,
            stage=Opportunity.Stage.FAMILY_ENROLLED,
        )

        with self.assertRaisesMessage(ValidationError, "Choose a stage that belongs"):
            deal.full_clean()

    def test_deal_advance_api_rejects_probability_outside_valid_range(self):
        deal = Opportunity.objects.create(
            lead=self.lead,
            name="Donor relationship",
            pipeline=Opportunity.Pipeline.FOUNDATION_DONORS,
            stage=Opportunity.Stage.DONOR_IDENTIFIED,
            probability=10,
        )
        api_client = APIClient()
        api_client.force_authenticate(self.admin_user)

        response = api_client.post(
            f"/api/v1/opportunities/{deal.pk}/advance/",
            {"stage": Opportunity.Stage.DONOR_CULTIVATION, "probability": 101},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        deal.refresh_from_db()
        self.assertEqual(deal.stage, Opportunity.Stage.DONOR_IDENTIFIED)
        self.assertEqual(deal.probability, 10)

    def test_company_workspace_shows_contacts_and_deals(self):
        company = Company.objects.create(name="Community Partners LLC")
        self.lead.company = company
        self.lead.save(update_fields=["company", "updated_at"])
        Opportunity.objects.create(
            lead=self.lead,
            company=company,
            name="Referral relationship",
            pipeline=Opportunity.Pipeline.REFERRAL_PARTNERS,
            stage=Opportunity.Stage.PARTNER_IDENTIFIED,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("crm_company_detail", args=[company.pk]))
        list_response = self.client.get(reverse("crm_company_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alex Reader")
        self.assertContains(response, "Community Partners LLC")
        self.assertContains(list_response, "Community Partners LLC")

    def test_family_deals_are_named_per_student_and_term(self):
        first = Opportunity(
            lead=self.lead,
            pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
            stage=Opportunity.Stage.FAMILY_LEAD_NURTURE,
            student_name="Jacob Reader",
            term_year="Fall 2027",
        )
        second = Opportunity(
            lead=self.lead,
            pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
            stage=Opportunity.Stage.FAMILY_WAITLIST,
            student_name="Maya Reader",
            term_year="Fall 2027",
        )
        first.full_clean()
        first.save()
        second.full_clean()
        second.save()

        self.assertEqual(self.lead.opportunities.count(), 2)
        self.assertEqual(first.name, "Jacob Reader — Fall 2027")
        self.assertEqual(second.name, "Maya Reader — Fall 2027")

    def test_one_company_can_have_separate_named_grant_and_equity_deals(self):
        company = Company.objects.create(name="North Star Foundation")
        grant = Opportunity(
            company=company,
            pipeline=Opportunity.Pipeline.FOUNDATION_GRANTS,
            stage=Opportunity.Stage.GRANT_NEED_INTRO,
            program_name="Reading Access",
            cycle_year=2027,
            capital_lane=Opportunity.CapitalLane.FOUNDATION,
        )
        investment = Opportunity(
            company=company,
            pipeline=Opportunity.Pipeline.EQUITY_INVESTMENT,
            stage=Opportunity.Stage.EQUITY_NEED_INTRO,
            investment_round="Seed",
            capital_lane=Opportunity.CapitalLane.COMPANY,
        )
        grant.full_clean()
        grant.save()
        investment.full_clean()
        investment.save()
        grant.related_deals.add(investment)

        self.assertEqual(company.deals.count(), 2)
        self.assertEqual(grant.name, "North Star Foundation — Reading Access — 2027")
        self.assertEqual(investment.name, "North Star Foundation — Seed")
        self.assertEqual(grant.related_deals.get(), investment)

    def test_remove_from_each_pipeline_preserves_contact_and_other_deals(self):
        self.client.force_login(self.admin_user)
        sibling = Opportunity.objects.create(
            lead=self.lead, name="Keep this enrollment",
            pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
            stage=Opportunity.Stage.FAMILY_LEAD_NURTURE,
        )
        for pipeline in Opportunity.Pipeline.values:
            with self.subTest(pipeline=pipeline):
                deal = Opportunity.objects.create(
                    lead=self.lead, name=f"Remove {pipeline}", pipeline=pipeline,
                    stage=Opportunity.initial_stage_for_pipeline(pipeline),
                    metadata={"needs_naming_review": True},
                )
                deal.related_deals.add(sibling)
                url = reverse("crm_deal_remove", args=[deal.pk])
                board_url = reverse("crm_deal_list") + f"?pipeline={pipeline}"
                self.assertContains(self.client.get(board_url), url)
                self.assertContains(self.client.get(reverse("crm_deal_detail", args=[deal.pk])), url)
                self.assertContains(self.client.get(url), deal.name)
                deal.refresh_from_db()
                self.assertFalse(deal.is_deleted)
                self.assertRedirects(self.client.post(url), board_url)
                deal.refresh_from_db()
                sibling.refresh_from_db()
                self.lead.refresh_from_db()
                self.assertTrue(deal.is_deleted)
                self.assertIsNotNone(deal.deleted_at)
                self.assertTrue(deal.needs_naming_review)
                self.assertFalse(sibling.is_deleted)
                self.assertFalse(self.lead.is_deleted)
                self.assertEqual(self.lead.contact_email, "alex@example.com")
                self.assertTrue(deal.related_deals.filter(pk=sibling.pk).exists())
                self.assertNotContains(self.client.get(board_url), deal.name)
                self.assertNotContains(
                    self.client.get(reverse("crm_contact_detail", args=[self.lead.pk])),
                    reverse("crm_deal_detail", args=[deal.pk]),
                )
                self.assertEqual(self.client.get(reverse("crm_deal_detail", args=[deal.pk])).status_code, 404)
                self.assertEqual(self.client.post(url).status_code, 404)
                self.assertEqual(AuditLog.objects.filter(
                    action="crm.deal.removed", entity_id=str(deal.pk)
                ).count(), 1)

    def test_pipeline_removal_requires_crm_access_and_csrf(self):
        from django.test import Client

        deal = Opportunity.objects.create(
            lead=self.lead, name="Protected enrollment",
            pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
            stage=Opportunity.Stage.FAMILY_LEAD_NURTURE,
        )
        url = reverse("crm_deal_remove", args=[deal.pk])
        self.assertEqual(self.client.post(url).status_code, 302)
        self.client.force_login(self.guardian)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin_user)
        self.assertEqual(csrf_client.post(url).status_code, 403)
        deal.refresh_from_db()
        self.assertFalse(deal.is_deleted)
        self.assertFalse(AuditLog.objects.filter(action="crm.deal.removed").exists())

    def test_intake_enrollment_can_be_edited_before_naming_is_complete(self):
        self.client.force_login(self.admin_user)
        deal = Opportunity.objects.create(
            lead=self.lead, name="Alex Reader — Enrollment",
            pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
            stage=Opportunity.Stage.FAMILY_LEAD_NURTURE,
            metadata={"needs_naming_review": True, "source": "intake"},
        )
        url = reverse("crm_deal_detail", args=[deal.pk])
        self.assertContains(self.client.get(url), "Edit person: Alex Reader")
        data = {"lead": self.lead.pk, "stage": Opportunity.Stage.FAMILY_WAITLIST,
                "value": "0", "next_steps": "Call parent"}
        response = self.client.post(url, data)
        self.assertRedirects(response, url)
        deal.refresh_from_db()
        self.assertTrue(deal.needs_naming_review)
        self.assertEqual(deal.next_steps, "Call parent")
        self.assertEqual(deal.stage, Opportunity.Stage.FAMILY_WAITLIST)
        self.assertEqual(deal.name, "Alex Reader — Enrollment")

        data.update(student_name="Sam Reader")
        self.assertRedirects(self.client.post(url, data), url)
        deal.refresh_from_db()
        self.assertTrue(deal.needs_naming_review)
        data.update(term_year="Fall")
        self.assertEqual(self.client.post(url, data).status_code, 400)
        deal.refresh_from_db()
        self.assertTrue(deal.needs_naming_review)
        data.update(term_year="Fall 2026")
        self.assertRedirects(self.client.post(url, data), url)
        deal.refresh_from_db()
        self.assertFalse(deal.needs_naming_review)
        self.assertEqual(deal.metadata, {"source": "intake"})
        self.assertEqual(deal.name, "Sam Reader — Fall 2026")
        data.update(student_name="")
        self.assertEqual(self.client.post(url, data).status_code, 400)

    def test_enrollment_person_edit_validates_and_preserves_enrollment(self):
        self.client.force_login(self.admin_user)
        deal = Opportunity.objects.create(
            lead=self.lead, name="Alex Reader — Enrollment",
            pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
            stage=Opportunity.Stage.FAMILY_LEAD_NURTURE,
            metadata={"needs_naming_review": True},
        )
        url = reverse("crm_enrollment_person_edit", args=[deal.pk])
        self.assertContains(self.client.get(url), "Save person")
        data = {"contact_name": "Alex Updated", "contact_email": "NEW@example.com",
                "contact_phone": "555-0100"}
        self.assertRedirects(self.client.post(url, data), reverse("crm_deal_detail", args=[deal.pk]))
        self.lead.refresh_from_db()
        deal.refresh_from_db()
        self.assertEqual(self.lead.contact_name, "Alex Updated")
        self.assertEqual(self.lead.contact_email, "new@example.com")
        self.assertEqual(self.lead.contact_phone, "555-0100")
        self.assertTrue(deal.needs_naming_review)
        self.assertEqual(deal.lead_id, self.lead.pk)
        self.assertTrue(AuditLog.objects.filter(action="crm.contact.updated", entity_id=str(self.lead.pk)).exists())
        data["contact_email"] = "invalid"
        self.assertEqual(self.client.post(url, data).status_code, 400)
        other = Lead.objects.create(school_name="Other", contact_name="Other", contact_email="other@example.com")
        data["contact_email"] = other.contact_email
        self.assertEqual(self.client.post(url, data).status_code, 400)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.contact_email, "new@example.com")
        self.client.force_login(self.guardian)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.force_login(self.admin_user)
        self.lead.is_deleted = True
        self.lead.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_deal_form_enforces_and_generates_master_naming_convention(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("crm_deal_new"),
            {
                "lead": self.lead.pk,
                "pipeline": Opportunity.Pipeline.FAMILY_ENROLLMENT,
                "stage": Opportunity.Stage.FAMILY_CONSULTATION,
                "owner": self.admin_user.pk,
                "priority": Opportunity.Priority.HIGH,
                "student_name": "Jacob Reader",
                "term_year": "Fall 2027",
                "funding_type": Opportunity.FundingType.ESA,
                "esa_program": Opportunity.EsaProgram.FES_UA,
                "grade_band": Opportunity.GradeBand.PREK_2,
                "in_catchment_zip": "32801",
                "referral_source": "North Star Pediatrics",
                "segment_tags": "bilingual, IEP density",
                "value": "0",
            },
        )

        deal = Opportunity.objects.get()
        self.assertRedirects(response, reverse("crm_deal_detail", args=[deal.pk]))
        self.assertEqual(deal.name, "Jacob Reader — Fall 2027")
        self.assertEqual(deal.priority, Opportunity.Priority.HIGH)
        self.assertEqual(deal.segment_tags, "bilingual, IEP density")

    def test_recurring_deal_name_requires_a_four_digit_year(self):
        deal = Opportunity(
            lead=self.lead,
            pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
            stage=Opportunity.Stage.FAMILY_WAITLIST,
            student_name="Jacob Reader",
            term_year="Fall",
        )

        with self.assertRaisesMessage(ValidationError, "Include a four-digit year"):
            deal.full_clean()
