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

    def test_low_scores_require_all_sections_before_support_result(self):
        for grade in ["kindergarten", "grade_1", "grade_2", "grade_3"]:
            with self.subTest(grade=grade):
                spec = definition(grade)
                answers = {}
                for index, group in enumerate(spec["groups"]):
                    answers.update({q["id"]: False for q in group["questions"]})
                    result = evaluate(grade, answers)
                    self.assertEqual(
                        result["complete"], index == len(spec["groups"]) - 1
                    )
                self.assertEqual(result["outcome"], "support")
                self.assertEqual(result["answered"], result["total"])

    def test_cannot_skip_an_unanswered_section(self):
        groups = definition("grade_1")["groups"]
        with self.assertRaises(InventoryError):
            evaluate("grade_1", {groups[1]["questions"][0]["id"]: True})

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

    def test_contact_assessment_button_tracks_progress(self):
        now = timezone.now()
        cases = [
            (None, None, None, "pending", "Not started · View assessment"),
            (now, None, None, "started", "Started · View progress"),
            (now, now, None, "finished", "Finished · View results"),
            (now, now, now, "finished", "Finished · View results"),
        ]
        for started, completed, reviewed, color, label in cases:
            with self.subTest(label=label, reviewed=reviewed):
                self.invitation.started_at = started
                self.invitation.completed_at = completed
                self.invitation.reviewed_at = reviewed
                self.invitation.save()
                response = self.client.get(
                    reverse("crm_contact_detail", args=[self.parent.pk])
                )
                self.assertContains(response, f'assessment-action--{color}"')
                self.assertContains(response, label)
                self.assertContains(response, "Parent Reading Inventory · Avery")
                self.assertContains(
                    response,
                    f'href="{reverse("inventory_detail", args=[self.invitation.pk])}"',
                )
                self.assertContains(response, self.invitation.status)

    def test_public_get_does_not_start_inventory(self):
        response = Client().get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Referrer-Policy"], "same-origin")
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

    def test_full_completion_is_idempotent_and_queues_review_and_emails(self):
        for _ in definition(self.child.grade)["groups"]:
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

    def test_first_section_no_answers_do_not_finish_grade_one_inventory(self):
        self.child.grade = "grade_1"
        self.child.save()
        self.client.logout()
        self.post_group(value="no")
        self.invitation.refresh_from_db()
        self.assertEqual(len(self.invitation.answers), 7)
        self.assertIsNone(self.invitation.completed_at)
        self.assertEqual(self.invitation.current_group, 1)
        self.assertEqual(self.invitation.emails.count(), 0)
        response = self.client.get(self.url)
        self.assertContains(response, "Section 2 of")
        self.assertContains(response, 'type="radio"')
        for _ in definition(self.child.grade)["groups"][1:]:
            self.post_group(value="no")
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.result["answered"], 25)
        self.assertIsNotNone(self.invitation.completed_at)

    def test_legacy_completed_inventory_can_resume_without_losing_answers(self):
        from apps.crm.inventory import complete_inventory

        answers = {
            q["id"]: False
            for q in definition(self.child.grade)["groups"][0]["questions"]
        }
        self.invitation.answers = answers
        complete_inventory(
            self.invitation,
            {"complete": True, "outcome": "support", "answered": 8, "total": 24},
        )
        task_id = self.invitation.result["review_task_id"]
        slot = ConsultationSlot.objects.create(
            host=self.staff,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, minutes=30),
        )
        InventoryBooking.objects.create(
            invitation=self.invitation, slot=slot, phone="4075550123", timezone="UTC"
        )
        self.client.logout()
        self.assertContains(self.client.get(self.url), "Answer remaining questions")
        self.client.post(self.url, {"action": "resume"})
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.answers, answers)
        self.assertContains(
            self.client.get(
                reverse("inventory_booking", args=[token_for(self.invitation)])
            ),
            "Your consultation is booked.",
        )
        self.assertIsNone(self.invitation.completed_at)
        self.assertEqual(self.invitation.current_group, 1)
        for _ in definition(self.child.grade)["groups"][1:]:
            self.post_group(value="no")
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.result["answered"], 24)
        self.assertEqual(self.invitation.result["review_task_id"], task_id)
        self.assertEqual(CrmActivity.objects.filter(activity_type="task").count(), 1)
        self.assertNotContains(self.client.get(self.url), "Answer remaining questions")

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
        for _ in definition(self.child.grade)["groups"]:
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
        self.assertIn('aria-label="ClearCode Reading"', email.provider_message.body_html)
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

    def test_https_continue_and_save_preserve_csrf_verification(self):
        for action in ("save", "continue"):
            with self.subTest(action=action):
                client = Client(enforce_csrf_checks=True)
                response = client.get(self.url, secure=True)
                self.assertEqual(response["Referrer-Policy"], "same-origin")
                self.assertContains(
                    response, '<meta name="referrer" content="same-origin">'
                )
                self.invitation.refresh_from_db()
                revision = self.invitation.revision
                group = definition(self.child.grade)["groups"][
                    self.invitation.current_group
                ]
                payload = {q["id"]: "yes" for q in group["questions"]}
                payload.update(
                    revision=revision,
                    action=action,
                    csrfmiddlewaretoken=client.cookies["csrftoken"].value,
                )
                for headers in (
                    {},
                    {"HTTP_REFERER": "https://other.example/"},
                    {"HTTP_ORIGIN": "https://other.example/"},
                ):
                    self.assertEqual(
                        client.post(
                            self.url, payload, secure=True, **headers
                        ).status_code,
                        403,
                    )
                self.invitation.refresh_from_db()
                self.assertEqual(self.invitation.revision, revision)
                self.assertEqual(
                    client.post(
                        self.url,
                        payload,
                        secure=True,
                        HTTP_REFERER=f"https://testserver{self.url}",
                    ).status_code,
                    302,
                )
                self.invitation.refresh_from_db()
                self.assertEqual(self.invitation.revision, revision + 1)
                self.assertTrue(
                    all(self.invitation.answers[q["id"]] for q in group["questions"])
                )
                self.assertEqual(
                    self.invitation.current_group, 0 if action == "save" else 1
                )

    def test_https_booking_preserves_csrf_verification(self):
        for _ in definition(self.child.grade)["groups"]:
            self.post_group(value="no")
        slot = ConsultationSlot.objects.create(
            host=self.staff,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, minutes=30),
        )
        url = reverse("inventory_booking", args=[token_for(self.invitation)])
        client = Client(enforce_csrf_checks=True)
        response = client.get(url, secure=True)
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertContains(response, '<meta name="referrer" content="same-origin">')
        payload = {
            "slot": slot.pk,
            "phone": "407-555-0123",
            "timezone": "America/New_York",
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        }
        for headers in ({}, {"HTTP_REFERER": "https://other.example/"}):
            self.assertEqual(
                client.post(url, payload, secure=True, **headers).status_code, 403
            )
        self.assertFalse(
            InventoryBooking.objects.filter(invitation=self.invitation).exists()
        )
        self.assertEqual(
            client.post(
                url, payload, secure=True, HTTP_REFERER=f"https://testserver{url}"
            ).status_code,
            302,
        )
        self.assertEqual(
            InventoryBooking.objects.get(invitation=self.invitation).slot, slot
        )

    def test_review_closes_only_its_own_task(self):
        for _ in definition(self.child.grade)["groups"]:
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
    def test_all_confirmation_templates_share_unboxed_brand(self):
        brand = render_to_string("crm/_email_brand.html")
        self.assertIn("background:transparent", brand)
        self.assertNotIn("<img", brand)
        for template in ("crm/inventory_email.html", "crm/website_email.html"):
            with self.subTest(template=template):
                html = render_to_string(template, {"email": InventoryMail()})
                self.assertIn(brand, html)
                self.assertNotIn("cc-lockup-", html)

    @override_settings(PUBLIC_APP_URL="https://reading.example.com/")
    def test_button_precedes_signoff_with_escaped_content_and_shared_brand(self):
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
        self.assertIn('aria-label="ClearCode Reading"', html)
        self.assertNotIn("<img", html)
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


class ConsultationAvailabilityTests(TestCase):
    def setUp(self):
        self.bethany = get_user_model().objects.create_user(
            username="bethany",
            email="bethany@example.com",
            first_name="Bethany",
            last_name="Fleming",
            role="crm_user",
        )
        self.other = get_user_model().objects.create_user(
            username="other-host",
            email="other@example.com",
            role="crm_user",
        )
        self.admin = get_user_model().objects.create_user(
            username="calendar-admin",
            email="admin@example.com",
            is_superuser=True,
        )
        self.slot = ConsultationSlot.objects.create(
            host=self.bethany,
            active=False,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, minutes=30),
        )
        self.url = reverse("inventory_slots")
        self.client.force_login(self.bethany)

    def test_bethany_default_and_explicit_host_selection(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["selected_host"], self.bethany)
        self.assertEqual(list(response.context["slots"]), [self.slot])
        response = self.client.get(self.url, {"host": self.other.pk})
        self.assertEqual(list(response.context["slots"]), [])
        self.assertEqual(self.client.get(self.url, {"host": "bad"}).status_code, 404)

    def test_only_host_can_confirm_and_other_users_cannot_withdraw(self):
        for user in [self.other, self.admin]:
            self.client.force_login(user)
            self.assertEqual(
                self.client.post(
                    self.url, {"action": "confirm", "slot": self.slot.pk}
                ).status_code,
                403,
            )
        self.client.force_login(self.bethany)
        self.client.post(self.url, {"action": "confirm", "slot": self.slot.pk})
        self.slot.refresh_from_db()
        self.assertTrue(self.slot.active)
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.post(
                self.url, {"action": "withdraw", "slot": self.slot.pk}
            ).status_code,
            403,
        )
        self.client.force_login(self.bethany)
        self.client.post(self.url, {"action": "withdraw", "slot": self.slot.pk})
        self.slot.refresh_from_db()
        self.assertFalse(self.slot.active)

    def test_team_proposals_require_host_confirmation(self):
        self.client.force_login(self.admin)
        payload = {
            "host": self.bethany.pk,
            "date": (timezone.now() + timedelta(days=5)).date(),
            "time": "13:00",
            "timezone": "America/New_York",
            "duration": 30,
        }
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        self.assertFalse(ConsultationSlot.objects.exclude(pk=self.slot.pk).get().active)
        self.client.force_login(self.other)
        response = self.client.post(self.url, payload)
        self.assertIn("host", response.context["form"].errors)
        self.assertEqual(ConsultationSlot.objects.count(), 2)

    def test_cannot_confirm_overlapping_or_past_slot(self):
        ConsultationSlot.objects.create(
            host=self.bethany,
            active=True,
            starts_at=self.slot.starts_at,
            ends_at=self.slot.ends_at,
        )
        self.client.post(self.url, {"action": "confirm", "slot": self.slot.pk})
        self.slot.refresh_from_db()
        self.assertFalse(self.slot.active)
        self.slot.starts_at = timezone.now() - timedelta(hours=2)
        self.slot.ends_at = timezone.now() - timedelta(hours=1)
        self.slot.save()
        self.client.post(self.url, {"action": "confirm", "slot": self.slot.pk})
        self.slot.refresh_from_db()
        self.assertFalse(self.slot.active)

    def test_public_calendar_defaults_to_bethany_and_hides_unconfirmed_times(self):
        parent = Lead.objects.create(
            contact_name="Parent",
            contact_email="parent@example.com",
            school_name="Family",
        )
        child = InventoryChild.objects.create(
            parent=parent, name="Reader", grade="grade_1"
        )
        invitation = InventoryInvitation.objects.create(
            child=child,
            recipient=parent.contact_email,
            completed_at=timezone.now(),
            result={"outcome": "support"},
            expires_at=timezone.now() + timedelta(days=30),
        )
        other_slot = ConsultationSlot.objects.create(
            host=self.other, starts_at=self.slot.starts_at, ends_at=self.slot.ends_at
        )
        url = reverse("inventory_booking", args=[token_for(invitation)])
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.context["selected_host"], self.bethany)
        self.assertEqual(list(response.context["slots"]), [])
        response = self.client.get(url, {"host": self.other.pk})
        self.assertEqual(list(response.context["slots"]), [other_slot])
        response = self.client.post(
            url, {"slot": self.slot.pk, "phone": "4075550123", "timezone": "UTC"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(InventoryBooking.objects.exists())

    def test_ambiguous_bethany_name_does_not_choose_an_arbitrary_account(self):
        from apps.crm.consultations import default_consultation_host

        self.other.first_name = "Bethany"
        self.other.last_name = "Fleming"
        self.other.save()
        self.assertIsNone(default_consultation_host())
        with override_settings(
            CRM_DEFAULT_CONSULTATION_HOST_EMAIL="bethany@example.com"
        ):
            self.assertEqual(default_consultation_host(), self.bethany)
