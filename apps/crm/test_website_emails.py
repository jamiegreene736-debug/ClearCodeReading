from email import policy
from email.parser import BytesParser
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import RecruitingInterest
from apps.crm.models import FormSubmission, Lead, WebsiteReceipt
from apps.crm.services import LeadIntake, record_form_submission
from apps.crm.website_emails import enqueue_pending_receipts, receipt_context
from apps.crm_email.models import Mailbox, Message
from apps.crm_email.services import build_mime
from apps.crm_email.worker import accepted
from apps.users.models import CustomUser


@override_settings(
    PUBLIC_APP_URL="https://example.com",
    WEBSITE_EMAIL_SENDER="sender@clearcodereading.com",
)
class WebsiteEmailTests(TestCase):
    def setUp(self):
        user = CustomUser.objects.create_user(
            username="sender",
            email="sender@clearcodereading.com",
            role=CustomUser.Role.CRM_USER,
        )
        self.mailbox = Mailbox.objects.create(
            user=user, email=user.email, status="connected"
        )
        self.config = patch("apps.crm.website_emails.require_configured")
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_survey_post_queues_customer_confirmation_and_team_notice_once(self):
        self.client.post(reverse("crm_survey_submit"), {
            "source_path": "/survey/", "name": "Survey Visitor", "email": "visitor@example.com",
            "email_consent": "yes", "home_zip": "32789", "respondent_situation": "community_supporter",
            "engagement_interests": ["donor", "referral_partner"],
        })
        enqueue_pending_receipts()
        enqueue_pending_receipts()
        receipt = WebsiteReceipt.objects.get()
        self.assertEqual(receipt.customer_message.to, ["visitor@example.com"])
        self.assertEqual(receipt.customer_message.status, Message.Status.QUEUED)
        self.assertIn("survey", receipt.customer_message.subject)
        self.assertIn("Donor", receipt.customer_message.body_text)
        self.assertEqual(Message.objects.count(), 2)

    def submit(self, kind="website", **data):
        return record_form_submission(
            intake=LeadIntake(
                contact_email="parent@example.com",
                contact_name=data.get("name", "Parent"),
                school_name="Family",
                audience="parent",
            ),
            form_type=kind,
            source_path=data.pop("source_path", "/contact/"),
            submitted_data={"name": "Parent", "email": "parent@example.com", **data},
        )[1]

    def test_each_form_has_two_independent_branded_messages(self):
        for kind in FormSubmission.FormType.values:
            with self.subTest(kind=kind):
                submission = self.submit(kind)
                enqueue_pending_receipts()
                receipt = WebsiteReceipt.objects.get(submission=submission)
                customer, team = receipt.customer_message, receipt.team_message
                self.assertEqual(customer.to, ["parent@example.com"])
                self.assertEqual(team.to, ["info@clearcodereading.com"])
                self.assertEqual(customer.reply_to, "info@clearcodereading.com")
                self.assertEqual(team.reply_to, "parent@example.com")
                self.assertEqual(customer.status, "queued")
                self.assertIn("#F5A623", customer.body_html)
                self.assertIn("What happens next", customer.body_text)
                mime = BytesParser(policy=policy.default).parsebytes(
                    build_mime(customer)
                )
                self.assertEqual(mime["Reply-To"], "info@clearcodereading.com")
                self.assertEqual(
                    mime.get_body(preferencelist=("html",)).get_content_type(),
                    "text/html",
                )

    def test_worker_repeats_never_duplicate_or_resend_failed_messages(self):
        submission = self.submit()
        enqueue_pending_receipts()
        receipt = WebsiteReceipt.objects.get(submission=submission)
        Message.objects.filter(pk=receipt.customer_message_id).update(status="failed")
        enqueue_pending_receipts()
        self.assertEqual(Message.objects.count(), 2)
        receipt.customer_message.refresh_from_db()
        self.assertEqual(receipt.customer_message.status, "failed")

    def test_missing_mailbox_recovers_after_connection(self):
        submission = self.submit()
        self.mailbox.status = "disconnected"
        self.mailbox.save()
        enqueue_pending_receipts()
        self.assertTrue(WebsiteReceipt.objects.get(submission=submission).error)
        self.assertEqual(Message.objects.count(), 0)
        self.mailbox.status = "connected"
        self.mailbox.save()
        enqueue_pending_receipts()
        self.assertEqual(Message.objects.count(), 2)
        self.assertEqual(WebsiteReceipt.objects.get(submission=submission).error, "")

    def test_rollback_does_not_leave_receipts(self):
        with self.assertRaises(ValueError), transaction.atomic():
            self.submit()
            raise ValueError("abort")
        self.assertFalse(WebsiteReceipt.objects.exists())

    def test_new_submission_uses_snapshot_not_later_contact_edits(self):
        first = self.submit(role_interest="Teacher")
        first.lead.contact_email = "later@example.com"
        first.lead.save()
        enqueue_pending_receipts()
        self.assertEqual(
            WebsiteReceipt.objects.get(submission=first).customer_message.to,
            ["parent@example.com"],
        )

    def test_escape_html_and_keep_sensitive_assessment_data_in_crm(self):
        submission = self.submit(
            "assessment",
            name='<script>alert("x")</script>',
            notes="Private child details",
            digital_reading_result={"score": "SECRET_RESULT"},
        )
        enqueue_pending_receipts()
        receipt = WebsiteReceipt.objects.get(submission=submission)
        for message in (receipt.customer_message, receipt.team_message):
            self.assertNotIn("<script>", message.body_html)
            self.assertIn("&lt;script&gt;", message.body_html)
            self.assertNotIn("SECRET_RESULT", message.body_text)
            self.assertNotIn("Private child details", message.body_text)

    def test_resource_and_support_receipts_are_specific(self):
        resource = receipt_context(
            self.submit(resource_access="family_resources"), team=False
        )
        support = receipt_context(
            self.submit(source_path="/support/", support_topic="technical"), team=False
        )
        self.assertIn("resources", resource["subject"])
        self.assertTrue(resource["action_url"].endswith("/resources/"))
        self.assertIn("Support", support["subject"])
        self.assertIn(
            {"label": "Support Topic", "value": "Technical problem"}, support["rows"]
        )

    def test_survey_copy_follows_only_selected_interests(self):
        context = receipt_context(
            self.submit("survey", engagement_interests=["opening_updates"]), team=False
        )
        self.assertIn("updates", context["next_step"])
        self.assertNotIn("appointment", context["next_step"])
        self.assertNotIn("waitlist", context["next_step"])

    def test_public_valid_signup_enqueues_but_invalid_does_not(self):
        for path in ("/contact/", "/resources/", "/support/"):
            self.client.post(
                reverse("crm_signup"),
                {
                    "name": "Parent",
                    "email": "parent@example.com",
                    "redirect_to": path,
                    "notes": "Please contact me",
                },
            )
        self.assertEqual(WebsiteReceipt.objects.count(), 3)
        self.client.post(reverse("crm_signup"), {"name": "Parent", "email": "invalid"})
        self.assertEqual(WebsiteReceipt.objects.count(), 3)

    def test_newsletter_has_unsubscribe_link_and_honeypot_sends_nothing(self):
        self.client.post(
            reverse("newsletter_signup"),
            {"email": "parent@example.com", "consent": "yes", "redirect_to": "/"},
        )
        enqueue_pending_receipts()
        self.assertIn(
            "Unsubscribe", WebsiteReceipt.objects.get().customer_message.body_html
        )
        self.client.post(
            reverse("newsletter_signup"),
            {"email": "bot@example.com", "consent": "yes", "website": "bot"},
        )
        self.assertEqual(WebsiteReceipt.objects.count(), 1)

    def test_both_messages_roll_back_if_rendering_fails(self):
        self.submit()
        with (
            patch(
                "apps.crm.website_emails.receipt_context",
                side_effect=[
                    receipt_context(FormSubmission.objects.get(), team=False),
                    ValueError("bad template"),
                ],
            ),
            self.assertRaises(ValueError),
        ):
            enqueue_pending_receipts()
        self.assertEqual(Message.objects.count(), 0)
        self.assertIsNone(WebsiteReceipt.objects.get().customer_message_id)

    def test_career_delivery_preserves_recruiting_separation(self):
        application = RecruitingInterest.objects.create(
            name="Applicant",
            email="applicant@example.com",
            career_path="company",
            role_interest="Company team",
        )
        submission = FormSubmission.objects.create(
            form_type="career",
            source_path="/careers/",
            submitted_data={
                "name": application.name,
                "email": application.email,
                "application_id": application.pk,
                "role_interest": application.role_interest,
            },
        )
        WebsiteReceipt.objects.create(submission=submission)
        enqueue_pending_receipts()
        receipt = WebsiteReceipt.objects.get(submission=submission)
        self.assertFalse(Lead.objects.exists())
        self.assertEqual(receipt.customer_message.recruiting_interest, application)
        accepted(receipt.customer_message, "gmail123", "thread123")
        receipt.customer_message.refresh_from_db()
        self.assertEqual(receipt.customer_message.status, "sent")
        self.assertIsNone(receipt.customer_message.conversation)

    def test_removed_application_does_not_block_other_receipts(self):
        application = RecruitingInterest.objects.create(
            name="Applicant", email="applicant@example.com", career_path="company"
        )
        submission = FormSubmission.objects.create(
            form_type="career",
            source_path="/careers/",
            submitted_data={
                "email": application.email,
                "application_id": application.pk,
            },
        )
        receipt = WebsiteReceipt.objects.create(submission=submission)
        application.delete()
        valid = self.submit()
        enqueue_pending_receipts()
        receipt.refresh_from_db()
        self.assertIn("removed", receipt.error)
        self.assertIsNotNone(
            WebsiteReceipt.objects.get(submission=valid).customer_message
        )
