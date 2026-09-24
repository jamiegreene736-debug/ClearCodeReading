from typing import Any
from unittest.mock import MagicMock, patch

from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.crm.models import Lead, Opportunity
from apps.crm_email.models import Mailbox, Message, StageEmailDelivery, StageEmailPilot
from apps.crm_email.security import EmailError, encrypt
from apps.crm_email.stage_emails import (
    TEST_RECIPIENT,
    enqueue_stage_emails,
    render_copy,
    stage_send_allowed,
)
from apps.crm_email.stage_views import create_test, sample_deal
from apps.crm_email.tests import CONFIG
from apps.crm_email.worker import send
from apps.users.models import CustomUser


@override_settings(**CONFIG)
class StageEmailTests(TestCase):
    def setUp(self) -> None:
        self.user = CustomUser.objects.create_user(
            username="stage-admin",
            email="bethany@clearcodereading.com",
            password="test-password",
            role=CustomUser.Role.SUPER_ADMIN,
        )
        self.mailbox = Mailbox.objects.create(
            user=self.user,
            email=self.user.email,
            status="connected",
            signature="Bethany Fleming",
            encrypted_refresh_token=encrypt(b"test-token").decode(),
        )
        self.pilot = StageEmailPilot.objects.create(
            pk=1,
            mailbox=self.mailbox,
            enabled=True,
            scheduling_link="https://example.com/book",
            sample_investment_category="education",
        )
        self.client.force_login(self.user)

    def make_deal(
        self, pipeline: str = "family_enrollment", **kwargs: Any
    ) -> Opportunity:
        deal = sample_deal(pipeline, self.pilot)
        assert deal.lead is not None
        deal.lead.save()
        for key, value in kwargs.items():
            setattr(deal, key, value)
        deal.name = "Pilot test"
        deal.save()
        return deal

    def test_all_five_templates_queue_once_to_info(self) -> None:
        for pipeline in Opportunity.Pipeline.values:
            deal = self.make_deal(pipeline)
            deal.save()
        enqueue_stage_emails()
        enqueue_stage_emails()
        self.assertEqual(StageEmailDelivery.objects.count(), 5)
        self.assertEqual(Message.objects.count(), 5)
        for message in Message.objects.all():
            self.assertEqual(message.to, [TEST_RECIPIENT])
            self.assertEqual(message.cc, [])
            self.assertEqual(message.bcc, [])
            self.assertTrue(message.subject.startswith("[TEST] "))
            self.assertNotIn("{{", message.body_text)
            self.assertNotIn("[Name]", message.body_text)
            self.assertEqual(message.status, "queued")
            self.assertTrue(stage_send_allowed(message))

    def test_customer_deals_never_trigger(self) -> None:
        lead = Lead.objects.create(
            contact_name="Customer",
            contact_email="customer@example.com",
            school_name="Family",
        )
        for pipeline in Opportunity.Pipeline.values:
            self.make_deal(pipeline, lead=lead)
        enqueue_stage_emails()
        self.assertFalse(StageEmailDelivery.objects.exists())
        self.assertFalse(Message.objects.exists())

    def test_disabled_default_and_no_existing_deal_backfill(self) -> None:
        self.pilot.enabled = False
        self.pilot.save()
        deal = self.make_deal()
        self.pilot.enabled = True
        self.pilot.save()
        deal.save()
        enqueue_stage_emails()
        self.assertFalse(StageEmailDelivery.objects.exists())

    def test_later_stages_do_not_trigger(self) -> None:
        for pipeline in Opportunity.Pipeline.values:
            for stage, _ in Opportunity.stage_choices_for_pipeline(pipeline)[1:]:
                self.make_deal(pipeline, stage=stage)
        self.assertFalse(StageEmailDelivery.objects.exists())

    def test_stage_reentry_never_duplicates(self) -> None:
        deal = self.make_deal()
        enqueue_stage_emails()
        deal.stage = Opportunity.Stage.FAMILY_WAITLIST
        deal.save()
        deal.stage = Opportunity.Stage.FAMILY_LEAD_NURTURE
        deal.save()
        enqueue_stage_emails()
        self.assertEqual(Message.objects.count(), 1)

    def test_entering_first_stage_from_later_stage_triggers(self) -> None:
        deal = self.make_deal(stage=Opportunity.Stage.FAMILY_WAITLIST)
        self.assertFalse(StageEmailDelivery.objects.exists())
        deal.stage = Opportunity.Stage.FAMILY_LEAD_NURTURE
        deal.save()
        self.assertEqual(StageEmailDelivery.objects.count(), 1)

    def test_update_fields_does_not_capture_unsaved_stage(self) -> None:
        deal = self.make_deal(stage=Opportunity.Stage.FAMILY_WAITLIST)
        deal.stage = Opportunity.Stage.FAMILY_LEAD_NURTURE
        deal.name = "Renamed"
        deal.save(update_fields=["name"])
        self.assertFalse(StageEmailDelivery.objects.exists())

    def test_missing_fields_block_and_recover_without_duplicate(self) -> None:
        self.pilot.scheduling_link = ""
        self.pilot.save()
        self.make_deal()
        enqueue_stage_emails()
        self.assertFalse(Message.objects.exists())
        self.assertIn("scheduling link", StageEmailDelivery.objects.get().error)
        self.pilot.scheduling_link = "https://example.com/book"
        self.pilot.save()
        enqueue_stage_emails()
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(StageEmailDelivery.objects.get().error, "")

    def test_missing_equity_signature_and_category_are_explicit(self) -> None:
        self.mailbox.signature = ""
        self.mailbox.save()
        self.pilot.sample_investment_category = ""
        self.pilot.mailbox = self.mailbox
        copy = render_copy(sample_deal("equity_investment", self.pilot), self.pilot)
        self.assertIn("Investment category", copy.missing)
        self.assertIn("Equity sender signature", copy.missing)

    def test_approved_separate_document_wording_and_bethany_signature(self) -> None:
        copy = render_copy(sample_deal("foundation_grants", self.pilot), self.pilot)
        self.assertIn("other community focused literacy initiatives", copy.body)
        self.assertIn("Bethany Fleming, ClearCode Foundation", copy.body)
        self.assertIn("4. Foundation Grants", copy.source)
        family = render_copy(sample_deal("family_enrollment", self.pilot), self.pilot)
        self.assertIn("K–8 students", family.body)
        self.assertIn("bethany@clearcodereading.com", family.body)

    def test_queued_recipient_changes_are_blocked(self) -> None:
        self.make_deal()
        enqueue_stage_emails()
        message = Message.objects.get()
        message.to = ["customer@example.com"]
        message.save()
        client = MagicMock()
        send(client, message)
        self.assertEqual(message.status, "cancelled")
        client.send.assert_not_called()

    def test_leaving_stage_cancels_before_provider_send(self) -> None:
        deal = self.make_deal()
        enqueue_stage_emails()
        deal.stage = Opportunity.Stage.FAMILY_WAITLIST
        deal.save()
        message = Message.objects.get()
        client = MagicMock()
        send(client, message)
        self.assertEqual(message.status, "cancelled")
        client.send.assert_not_called()

    def test_pause_prevents_queued_send(self) -> None:
        self.make_deal()
        enqueue_stage_emails()
        self.pilot.enabled = False
        self.pilot.save()
        message = Message.objects.get()
        client = MagicMock()
        send(client, message)
        self.assertEqual(message.status, "cancelled")
        client.send.assert_not_called()

    def test_contact_email_change_cancels_before_queue(self) -> None:
        deal = self.make_deal()
        assert deal.lead is not None
        deal.lead.contact_email = "customer@example.com"
        deal.lead.save()
        enqueue_stage_emails()
        self.assertTrue(StageEmailDelivery.objects.get().cancelled)
        self.assertFalse(Message.objects.exists())

    def test_preview_is_private_and_has_five_templates(self) -> None:
        response = self.client.get(reverse("crm_first_stage_emails"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(len(response.context["previews"]), 5)
        self.assertContains(response, TEST_RECIPIENT)
        self.assertContains(response, "Customer delivery")

    def test_non_admin_cannot_configure_or_send(self) -> None:
        self.user.role = CustomUser.Role.CRM_USER
        self.user.save()
        for method in (self.client.get, self.client.post):
            response = method(reverse("crm_first_stage_emails"))
            self.assertEqual(response.status_code, 403)

    def test_test_button_is_idempotent_and_requires_valid_token(self) -> None:
        token = signing.dumps(
            {"pilot": self.pilot.pk, "pipeline": "referral_partners", "nonce": "one"},
            salt="first-stage-test",
        )
        first = create_test(self.pilot, "referral_partners", token)
        second = create_test(self.pilot, "referral_partners", token)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(StageEmailDelivery.objects.count(), 1)
        with self.assertRaises(EmailError):
            create_test(self.pilot, "family_enrollment", token)

    def test_template_values_are_escaped_in_html(self) -> None:
        self.pilot.foundation_name = '<script>alert("x")</script>'
        self.pilot.save()
        self.make_deal("foundation_donors")
        enqueue_stage_emails()
        self.assertNotIn("<script>", Message.objects.get().body_html)
        self.assertIn("&lt;script&gt;", Message.objects.get().body_html)

    def test_separate_equity_sender_is_enforced(self) -> None:
        user = CustomUser.objects.create_user(
            username="brook",
            email="brook@clearcodereading.com",
            role=CustomUser.Role.CRM_USER,
        )
        equity = Mailbox.objects.create(
            user=user,
            email=user.email,
            status="connected",
            signature="Brook",
            encrypted_refresh_token=encrypt(b"test-token").decode(),
        )
        self.pilot.equity_mailbox = equity
        self.pilot.save()
        self.make_deal("equity_investment")
        enqueue_stage_emails()
        message = Message.objects.get()
        self.assertEqual(message.sender, user.email)
        self.assertIn("Brook", message.body_text)
        self.assertTrue(stage_send_allowed(message))
        message.mailbox = self.mailbox
        self.assertFalse(stage_send_allowed(message))

    def test_admin_can_save_settings_and_pause_queued_delivery(self) -> None:
        self.make_deal()
        enqueue_stage_emails()
        response = self.client.post(
            reverse("crm_first_stage_emails"),
            {
                "action": "save",
                "mailbox": self.mailbox.pk,
                "sample_company": "Example",
                "foundation_name": "Bethany Fleming",
                "bethany_signature": "Bethany Fleming",
                "scheduling_link": "https://example.com/book",
            },
        )
        self.assertRedirects(response, reverse("crm_first_stage_emails"))
        self.pilot.refresh_from_db()
        self.assertFalse(self.pilot.enabled)
        self.assertEqual(Message.objects.get().status, "cancelled")

    def test_test_post_uses_designated_inbox_and_audits_actor(self) -> None:
        response = self.client.get(reverse("crm_first_stage_emails"))
        preview = response.context["previews"][1]
        response = self.client.post(
            reverse("crm_first_stage_emails"),
            {
                "action": "test",
                "pipeline": preview["pipeline"],
                "token": preview["token"],
                "recipient": "unapproved@example.com",
            },
        )
        self.assertRedirects(response, reverse("crm_first_stage_emails"))
        enqueue_stage_emails()
        self.assertEqual(Message.objects.get().to, [TEST_RECIPIENT])

    def test_insecure_scheduling_url_is_rejected(self) -> None:
        response = self.client.post(
            reverse("crm_first_stage_emails"),
            {
                "action": "save",
                "mailbox": self.mailbox.pk,
                "sample_company": "Example",
                "scheduling_link": "http://example.com/book",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "full HTTPS")
        self.pilot.refresh_from_db()
        self.assertEqual(self.pilot.scheduling_link, "https://example.com/book")

    def test_disconnected_sender_is_visible_in_delivery_log(self) -> None:
        self.mailbox.status = "disconnected"
        self.mailbox.save()
        self.make_deal()
        self.assertEqual(StageEmailDelivery.objects.count(), 1)
        enqueue_stage_emails()
        self.assertTrue(StageEmailDelivery.objects.get().cancelled)
        self.assertFalse(Message.objects.exists())

    def test_deal_and_capture_event_commit_together(self) -> None:
        with (
            patch(
                "apps.crm_email.stage_signals.StageEmailDelivery.objects.get_or_create",
                side_effect=RuntimeError("capture failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.make_deal()
        self.assertFalse(Opportunity.objects.exists())
        self.assertFalse(StageEmailDelivery.objects.exists())
