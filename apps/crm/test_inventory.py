import smtplib
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.crm.inventory import (
    InventoryError,
    definition,
    deliver_mail,
    evaluate,
    queue_mail,
    token_for,
)
from apps.crm.inventory_models import (
    ConsultationSlot,
    InventoryBooking,
    InventoryChild,
    InventoryInvitation,
    InventoryMail,
)
from apps.crm.models import CrmActivity, Lead


class InventoryScoringTests(SimpleTestCase):
    def answers(self, grade, value=True):
        return {
            q["id"]: value
            for group in definition(grade)["groups"]
            for q in group["questions"]
        }

    def test_counts_and_all_yes(self):
        for grade, count in [
            ("kindergarten", 20),
            ("grade_1", 25),
            ("grade_2", 25),
            ("grade_3", 24),
        ]:
            with self.subTest(grade=grade):
                result = evaluate(grade, self.answers(grade))
                self.assertEqual(result["total"], count)
                self.assertEqual(result["outcome"], "resources")

    def test_section_stop_precedes_total(self):
        spec = definition("kindergarten")
        answers = {q["id"]: False for q in spec["groups"][0]["questions"]}
        result = evaluate("kindergarten", answers)
        self.assertEqual(result["outcome"], "support")
        self.assertEqual(result["answered"], 11)
        answers[spec["groups"][1]["questions"][0]["id"]] = True
        with self.assertRaises(InventoryError):
            evaluate("kindergarten", answers)

    def test_boundary_total_routes_to_review(self):
        for grade in ["kindergarten", "grade_1", "grade_2", "grade_3"]:
            spec = definition(grade)
            answers = self.answers(grade, False)
            remaining = spec["resourceAt"] - 1
            for group in spec["groups"]:
                minimum = group.get("continueAt", 0)
                for q in group["questions"][:minimum]:
                    answers[q["id"]] = True
                    remaining -= 1
            # K's minimum stopping thresholds exceed 12, so that boundary is unreachable.
            if remaining < 0:
                continue
            for key in answers:
                if remaining and not answers[key]:
                    answers[key], remaining = True, remaining - 1
            self.assertEqual(evaluate(grade, answers)["outcome"], "review")

    def test_wrong_grade_or_values_are_rejected(self):
        for answers in [
            {"unknown": True},
            {"kindergarten-01": "yes"},
            {"kindergarten-01": 1},
        ]:
            with self.assertRaises(InventoryError):
                evaluate("kindergarten", answers)
        with self.assertRaises(InventoryError):
            definition("unlisted")

    def test_incomplete_is_not_scored(self):
        self.assertFalse(evaluate("grade_3", {"third-plus-01": True})["complete"])


@override_settings(
    DEBUG=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PUBLIC_APP_URL="https://clearcode.example",
)
class InventoryWorkflowTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="inventory-staff",
            email="inventory-staff@example.com",
            password="testing-pass",
            role="crm_user",
        )
        self.parent = Lead.objects.create(
            contact_name="Parent",
            contact_email="parent@example.com",
            school_name="Family",
            assigned_to=self.staff,
        )
        self.child = InventoryChild.objects.create(
            parent=self.parent, name="Avery", grade="grade_3"
        )
        self.invitation = InventoryInvitation.objects.create(
            child=self.child,
            recipient=self.parent.contact_email,
            created_by=self.staff,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.url = reverse("inventory_public", args=[token_for(self.invitation)])
        self.client.force_login(self.staff)

    def post_group(self, value="yes", action="continue"):
        self.invitation.refresh_from_db()
        group = definition(self.child.grade)["groups"][self.invitation.current_group]
        payload = {q["id"]: value for q in group["questions"]}
        payload.update(revision=self.invitation.revision, action=action)
        return self.client.post(self.url, payload)

    def test_contact_and_overview_have_assessment_actions(self):
        self.assertContains(
            self.client.get(reverse("crm_contact_detail", args=[self.parent.pk])),
            "Send assessment",
        )
        self.assertContains(self.client.get(reverse("inventory_list")), "Avery")

    def test_public_get_does_not_start_inventory(self):
        response = Client().get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", response["Cache-Control"])
        self.invitation.refresh_from_db()
        self.assertIsNone(self.invitation.started_at)

    def test_private_views_require_crm_permission(self):
        for name, args in [
            ("inventory_list", []),
            ("inventory_detail", [self.invitation.pk]),
            ("inventory_send", [self.parent.pk]),
            ("inventory_slots", []),
            ("inventory_preview", []),
        ]:
            self.assertEqual(Client().get(reverse(name, args=args)).status_code, 302)
        outsider = get_user_model().objects.create_user(
            username="inventory-outsider",
            email="outsider@example.com",
            password="pass",
            role="parent",
        )
        self.client.force_login(outsider)
        self.assertEqual(
            self.client.get(
                reverse("inventory_detail", args=[self.invitation.pk])
            ).status_code,
            403,
        )

    def test_invitation_send_is_idempotent_and_delivers_private_link(self):
        url = reverse("inventory_send", args=[self.parent.pk])
        response = self.client.get(url)
        payload = {
            "nonce": response.context["nonce"],
            "child": self.child.pk,
            "grade": self.child.grade,
            "recipient": self.parent.contact_email,
            "subject": "Reading inventory",
            "message": "Please complete the inventory.",
        }
        self.client.post(url, payload)
        sent_at = (
            InventoryInvitation.objects.exclude(pk=self.invitation.pk).get().sent_at
        )
        self.client.post(url, payload)
        self.assertEqual(
            InventoryInvitation.objects.exclude(pk=self.invitation.pk).get().sent_at,
            sent_at,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/reading-inventory/", mail.outbox[0].body)
        self.assertEqual(InventoryInvitation.objects.count(), 2)
        self.assertIsNotNone(
            InventoryInvitation.objects.exclude(pk=self.invitation.pk).get().sent_at
        )

    def test_other_parents_child_is_rejected(self):
        other = Lead.objects.create(
            contact_name="Other",
            contact_email="other@example.com",
            school_name="Family",
        )
        child = InventoryChild.objects.create(
            parent=other, name="Other child", grade="grade_3"
        )
        url = reverse("inventory_send", args=[self.parent.pk])
        response = self.client.get(url)
        result = self.client.post(
            url,
            {
                "nonce": response.context["nonce"],
                "child": child.pk,
                "grade": "grade_3",
                "recipient": self.parent.contact_email,
                "subject": "Test",
                "message": "Test",
            },
        )
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.context["form"].errors)
        self.assertEqual(InventoryInvitation.objects.count(), 1)

    def test_save_full_section_does_not_advance_or_submit(self):
        self.post_group(action="save")
        self.invitation.refresh_from_db()
        self.assertEqual(len(self.invitation.answers), 8)
        self.assertEqual(self.invitation.current_group, 0)
        self.assertIsNone(self.invitation.completed_at)
        self.assertContains(self.client.get(self.url), "checked")
        self.post_group()
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.current_group, 1)

    def test_partial_save_and_stale_tab(self):
        self.client.post(
            self.url, {"revision": 0, "action": "save", "third-plus-01": "yes"}
        )
        response = self.client.post(
            self.url, {"revision": 0, "action": "save", "third-plus-01": "no"}
        )
        self.assertContains(response, "changed in another tab")
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.answers, {"third-plus-01": True})

    def test_cannot_continue_incomplete(self):
        response = self.client.post(
            self.url, {"revision": 0, "action": "continue", "third-plus-01": "yes"}
        )
        self.assertContains(response, "answer every question")
        self.invitation.refresh_from_db()
        self.assertIsNone(self.invitation.completed_at)

    def test_stop_completion_is_idempotent_and_queues_review_and_emails(self):
        self.post_group(value="no")
        self.invitation.refresh_from_db()
        self.assertIsNotNone(self.invitation.completed_at)
        self.assertEqual(self.invitation.result["outcome"], "support")
        self.assertEqual(self.invitation.emails.count(), 2)
        self.assertEqual(CrmActivity.objects.filter(activity_type="task").count(), 1)
        self.client.post(self.url, {"revision": 0, "action": "continue"})
        self.assertEqual(self.invitation.emails.count(), 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertContains(self.client.get(self.url), "Schedule a consultation")

    def test_siblings_do_not_overwrite_results(self):
        sibling = InventoryChild.objects.create(
            parent=self.parent, name="Sibling", grade="grade_1"
        )
        second = InventoryInvitation.objects.create(
            child=sibling,
            recipient=self.parent.contact_email,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.post_group(value="no")
        second.refresh_from_db()
        self.assertEqual(second.answers, {})
        self.assertIsNone(second.completed_at)

    def test_expired_revoked_and_tampered_links(self):
        self.assertEqual(Client().get(self.url + "invalid").status_code, 404)
        self.invitation.expires_at = timezone.now() - timedelta(seconds=1)
        self.invitation.save()
        self.assertEqual(Client().get(self.url).status_code, 404)
        self.invitation.expires_at = timezone.now() + timedelta(days=1)
        self.invitation.revoked_at = timezone.now()
        self.invitation.save()
        self.assertEqual(Client().get(self.url).status_code, 404)

    def test_mail_transport_uncertainty_does_not_auto_retry(self):
        email = queue_mail(
            self.invitation, "test", "parent@example.com", "Subject", "Message"
        )
        with patch(
            "apps.crm.inventory.EmailMultiAlternatives.send", side_effect=TimeoutError
        ):
            deliver_mail(email.pk)
        email.refresh_from_db()
        self.assertEqual(email.status, "sending")
        with patch("apps.crm.inventory.EmailMultiAlternatives.send") as sender:
            deliver_mail(email.pk)
        sender.assert_not_called()

    def test_mail_rejected_and_zero_acceptance(self):
        for error in [
            smtplib.SMTPRecipientsRefused({"parent@example.com": (550, b"no")}),
            None,
        ]:
            email = queue_mail(
                self.invitation,
                f"test-{error}",
                "parent@example.com",
                "Subject",
                "Message",
            )
            with patch(
                "apps.crm.inventory.EmailMultiAlternatives.send",
                side_effect=error,
                return_value=0,
            ):
                deliver_mail(email.pk)
            email.refresh_from_db()
            self.assertEqual(email.status, "failed")

    @override_settings(DEBUG=False)
    def test_production_console_backend_cannot_report_success(self):
        email = queue_mail(
            self.invitation, "test", "parent@example.com", "Subject", "Message"
        )
        deliver_mail(email.pk)
        email.refresh_from_db()
        self.assertEqual(email.status, "failed")

    def test_preview_has_no_side_effects(self):
        counts = (
            InventoryInvitation.objects.count(),
            InventoryMail.objects.count(),
            CrmActivity.objects.count(),
        )
        self.client.post(
            reverse("inventory_preview"),
            {
                "grade": "grade_3",
                **{
                    q["id"]: "no"
                    for q in definition("grade_3")["groups"][0]["questions"]
                },
            },
        )
        self.assertEqual(
            counts,
            (
                InventoryInvitation.objects.count(),
                InventoryMail.objects.count(),
                CrmActivity.objects.count(),
            ),
        )

    def test_booking_and_calendar_idempotency(self):
        self.post_group(value="no")
        slot = ConsultationSlot.objects.create(
            host=self.staff,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, minutes=30),
        )
        url = reverse("inventory_booking", args=[token_for(self.invitation)])
        payload = {
            "slot": slot.pk,
            "phone": "407-555-0123",
            "timezone": "America/New_York",
        }
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(InventoryBooking.objects.count(), 1)
        emails = self.invitation.emails.filter(key__startswith="booking-")
        self.assertEqual(emails.count(), 2)
        for email in emails:
            self.assertIn("BEGIN:VCALENDAR", email.calendar)
            self.assertEqual(email.status, "sent")
        self.assertNotContains(
            self.client.get(reverse("inventory_slots")), "Withdraw time"
        )

    def test_booked_slot_cannot_be_taken_by_another_invitation(self):
        self.test_booking_and_calendar_idempotency()
        sibling = InventoryInvitation.objects.create(
            child=self.child,
            recipient="parent@example.com",
            completed_at=timezone.now(),
            result={"outcome": "support"},
            expires_at=timezone.now() + timedelta(days=30),
        )
        slot = ConsultationSlot.objects.get()
        response = self.client.post(
            reverse("inventory_booking", args=[token_for(sibling)]),
            {"slot": slot.pk, "phone": "407-555-0123", "timezone": "America/New_York"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(InventoryBooking.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventoryBooking.objects.create(
                invitation=sibling, slot=slot, phone="1234567", timezone="UTC"
            )

    def test_overlapping_availability_is_rejected(self):
        date = (timezone.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        payload = {
            "host": self.staff.pk,
            "date": date,
            "time": "13:00",
            "timezone": "America/New_York",
            "duration": 30,
        }
        url = reverse("inventory_slots")
        self.client.post(url, payload)
        response = self.client.post(url, {**payload, "time": "13:15"})
        self.assertContains(response, "overlaps")
        self.assertEqual(ConsultationSlot.objects.count(), 1)

    @override_settings(CRM_EMAIL_ENABLED=True)
    def test_google_queue_uses_creator_and_mirrors_receipt(self):
        from apps.crm_email.models import Mailbox, Message

        mailbox = Mailbox.objects.create(
            user=self.staff, email=self.staff.email, status="connected"
        )
        email = queue_mail(
            self.invitation,
            "inventory_send_test",
            self.parent.contact_email,
            "Inventory",
            "Return using the same link.\n\nThank you,\nThe ClearCode Reading team",
            "https://example.com/reading-inventory/test/",
            "Complete assessment",
        )
        with (
            patch("apps.crm.inventory_mail.require_configured"),
            patch("apps.crm.inventory_mail.active_mailbox", return_value=mailbox),
        ):
            deliver_mail(email.pk)
        email.refresh_from_db()
        self.assertEqual(email.status, "queued")
        self.assertEqual(email.provider_message.mailbox, mailbox)
        for content in (
            email.provider_message.body_html,
            email.provider_message.body_text,
        ):
            self.assertLess(
                content.index("same link."), content.index("Complete assessment")
            )
            self.assertLess(
                content.index("Complete assessment"), content.index("Thank you,")
            )
        self.assertIn("cc-lockup-linen-ui.png", email.provider_message.body_html)
        self.assertEqual(Message.objects.count(), 1)
        self.assertIsNone(self.invitation.sent_at)
        message = email.provider_message
        message.status, message.sent_at = "sent", timezone.now()
        message.save()
        email.refresh_from_db()
        self.invitation.refresh_from_db()
        self.assertEqual(email.status, "sent")
        self.assertIsNotNone(self.invitation.sent_at)
        self.assertEqual(Message.objects.count(), 1)

    @override_settings(CRM_EMAIL_ENABLED=True)
    def test_google_missing_setup_is_visible_failure(self):
        from apps.crm_email.security import EmailError

        email = queue_mail(
            self.invitation,
            "inventory_send_test",
            self.parent.contact_email,
            "Inventory",
            "Message",
        )
        with patch(
            "apps.crm.inventory_mail.require_configured",
            side_effect=EmailError("Connect Google email."),
        ):
            deliver_mail(email.pk)
        email.refresh_from_db()
        self.assertEqual(email.status, "failed")
        self.assertIsNone(email.provider_message_id)

    def test_revoked_link_email_is_not_sent(self):
        self.invitation.revoked_at = timezone.now()
        self.invitation.save()
        email = queue_mail(
            self.invitation,
            "inventory_send_test",
            self.parent.contact_email,
            "Inventory",
            "Message",
            "https://clearcode.example/reading-inventory/token/",
        )
        deliver_mail(email.pk)
        email.refresh_from_db()
        self.assertEqual(email.status, "failed")

    def test_only_sender_can_resend_from_their_mailbox(self):
        other = get_user_model().objects.create_user(
            username="other-sender",
            email="other-staff@example.com",
            password="test",
            role="crm_user",
        )
        self.client.force_login(other)
        self.client.post(
            reverse("inventory_detail", args=[self.invitation.pk]), {"action": "resend"}
        )
        self.assertEqual(self.invitation.emails.count(), 0)

    @override_settings(CRM_EMAIL_ENABLED=True)
    def test_google_calendar_uses_calendar_mime(self):
        from email import policy
        from email.parser import BytesParser

        from apps.crm_email.models import Mailbox
        from apps.crm_email.services import build_mime

        mailbox = Mailbox.objects.create(
            user=self.staff, email=self.staff.email, status="connected"
        )
        calendar = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nEND:VCALENDAR\r\n"
        )
        email = queue_mail(
            self.invitation,
            "booking-parent",
            self.parent.contact_email,
            "Consultation",
            "Booked",
            calendar=calendar,
        )
        with (
            patch("apps.crm.inventory_mail.require_configured"),
            patch("apps.crm.inventory_mail.active_mailbox", return_value=mailbox),
            patch("apps.crm.inventory_mail.encrypt", side_effect=lambda value: value),
        ):
            deliver_mail(email.pk)
        email.refresh_from_db()
        with patch("apps.crm_email.services.decrypt", side_effect=lambda value: value):
            mime = BytesParser(policy=policy.default).parsebytes(
                build_mime(email.provider_message)
            )
        attachment = next(mime.iter_attachments())
        self.assertEqual(attachment.get_content_type(), "text/calendar")
        self.assertEqual(attachment.get_param("method"), "REQUEST")

    def test_public_submission_requires_csrf(self):
        self.assertEqual(
            Client(enforce_csrf_checks=True)
            .post(self.url, {"revision": 0, "action": "continue"})
            .status_code,
            403,
        )

    def test_review_closes_only_its_own_task(self):
        self.post_group(value="no")
        other = CrmActivity.objects.create(
            lead=self.parent,
            activity_type="task",
            subject="Another task",
            due_at=timezone.now(),
        )
        self.client.post(
            reverse("inventory_detail", args=[self.invitation.pk]), {"action": "review"}
        )
        self.invitation.refresh_from_db()
        self.assertTrue(
            CrmActivity.objects.get(
                pk=self.invitation.result["review_task_id"]
            ).completed_at
        )
        other.refresh_from_db()
        self.assertIsNone(other.completed_at)

    @override_settings(CRM_EMAIL_ENABLED=True)
    def test_worker_recovers_pending_inventory_delivery(self):
        from apps.crm_email.worker import run_pass

        email = queue_mail(
            self.invitation,
            "inventory_send_recovery",
            self.parent.contact_email,
            "Inventory",
            "Message",
        )
        with (
            patch("apps.crm_email.worker.require_configured"),
            patch("apps.crm.inventory_mail.enqueue_google") as enqueue,
        ):
            run_pass()
        enqueue.assert_called_once_with(email.pk)

    def test_invalid_send_has_prominent_failure_and_preserves_entries(self):
        url = reverse("inventory_send", args=[self.parent.pk])
        nonce = self.client.get(url).context["nonce"]
        response = self.client.post(
            url,
            {
                "nonce": nonce,
                "recipient": "not-an-email",
                "subject": "My invitation",
                "message": "Please keep this message.",
                "grade": "grade_3",
            },
        )
        self.assertContains(response, "Assessment invitation not sent")
        self.assertContains(response, 'id="invitation-errors"')
        self.assertContains(response, "Please keep this message.")
        self.assertEqual(self.invitation.emails.count(), 0)
        self.assertEqual(InventoryInvitation.objects.count(), 1)

    def test_sent_confirmation_shows_recipient_and_receipt(self):
        url = reverse("inventory_send", args=[self.parent.pk])
        nonce = self.client.get(url).context["nonce"]
        response = self.client.post(
            url,
            {
                "nonce": nonce,
                "child": self.child.pk,
                "grade": self.child.grade,
                "recipient": self.parent.contact_email,
                "subject": "Inventory",
                "message": "Complete your inventory.",
            },
            follow=True,
        )
        self.assertContains(response, "Assessment email sent")
        self.assertContains(response, self.parent.contact_email)
        self.assertContains(response, "provider confirmed sending")
        self.assertFalse(response.context["feedback"].refresh)

    @override_settings(DEBUG=False)
    def test_send_setup_failure_is_prominent_on_redirect(self):
        url = reverse("inventory_send", args=[self.parent.pk])
        nonce = self.client.get(url).context["nonce"]
        response = self.client.post(
            url,
            {
                "nonce": nonce,
                "child": self.child.pk,
                "grade": self.child.grade,
                "recipient": self.parent.contact_email,
                "subject": "Inventory",
                "message": "Complete your inventory.",
            },
            follow=True,
        )
        self.assertContains(response, "Assessment email not sent")
        self.assertContains(response, "Your assessment is saved")
        self.assertContains(response, "Check email settings")

    def test_delivery_status_refresh_tracks_actual_receipt_not_other_mail(self):
        email = queue_mail(
            self.invitation,
            "inventory_send_feedback",
            self.parent.contact_email,
            "Inventory",
            "Message",
        )
        InventoryMail.objects.filter(pk=email.pk).update(status="queued")
        owner_mail = queue_mail(
            self.invitation, "owner", self.staff.email, "Notice", "Message"
        )
        InventoryMail.objects.filter(pk=owner_mail.pk).update(status="sent")
        url = (
            reverse("inventory_detail", args=[self.invitation.pk])
            + "?delivery_status=1"
        )
        response = self.client.get(url)
        self.assertIn("Assessment email queued", response.json()["html"])
        self.assertTrue(response.json()["refresh"])
        self.assertIn("Queued", response.json()["history"])
        self.assertIn("no-store", response["Cache-Control"])
        InventoryMail.objects.filter(pk=email.pk).update(
            status="sent", sent_at=timezone.now()
        )
        response = self.client.get(url)
        self.assertIn("Assessment email sent", response.json()["html"])
        self.assertFalse(response.json()["refresh"])
        self.assertNotIn("Queued", response.json()["history"])

    def test_uncertain_send_is_not_shown_as_sent(self):
        email = queue_mail(
            self.invitation,
            "inventory_send_feedback",
            self.parent.contact_email,
            "Inventory",
            "Message",
        )
        InventoryMail.objects.filter(pk=email.pk).update(status="sending")
        response = self.client.get(
            reverse("inventory_detail", args=[self.invitation.pk])
        )
        self.assertContains(response, "sending confirmation pending")
        self.assertNotContains(response, "Assessment email sent")

    def test_status_endpoint_requires_crm_access(self):
        url = (
            reverse("inventory_detail", args=[self.invitation.pk])
            + "?delivery_status=1"
        )
        self.assertEqual(Client().get(url).status_code, 302)
        outsider = get_user_model().objects.create_user(
            username="status-outsider",
            email="status-outsider@example.com",
            role="parent",
        )
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(url).status_code, 403)


class InventoryEmailLayoutTests(SimpleTestCase):
    @override_settings(PUBLIC_APP_URL="https://reading.example.com/")
    def test_button_precedes_signoff_with_escaped_content_and_absolute_logo(self):
        from apps.crm.inventory_email import plain_text

        email = InventoryMail(
            subject="Reading inventory",
            body="Hi <Parent>,\r\n\r\nReturn using the same link.\r\n\r\nThank you,\r\nThe team",
            action_url="https://reading.example.com/reading-inventory/test/",
            action_label="Complete assessment",
        )
        html = render_to_string("crm/inventory_email.html", {"email": email})
        for content in (html, plain_text(email)):
            self.assertLess(
                content.index("same link."), content.index("Complete assessment")
            )
            self.assertLess(
                content.index("Complete assessment"), content.index("Thank you,")
            )
        self.assertIn(
            "https://reading.example.com/assets/logo/cc-lockup-linen-ui.png", html
        )
        self.assertIn('alt="ClearCode Reading"', html)
        self.assertIn("&lt;Parent&gt;", html)
        self.assertNotIn("<Parent>", html)

    def test_custom_message_without_signoff_keeps_all_text_before_action(self):
        from apps.crm.inventory_email import plain_text, split_signoff

        body = "Thank you for your interest.\n\nPlease complete the survey."
        self.assertEqual(split_signoff(body), (body, ""))
        email = InventoryMail(
            body=body, action_url="https://example.com", action_label="Open"
        )
        self.assertEqual(plain_text(email), body + "\n\nOpen: https://example.com")
        email.action_url = ""
        self.assertEqual(plain_text(email), body)
        html = render_to_string("crm/inventory_email.html", {"email": email})
        self.assertIn("Please complete the survey.", html)
