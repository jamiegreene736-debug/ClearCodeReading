import base64
import json
import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.crm.models import CrmActivity, Lead
from apps.crm_email.forms import ComposeForm
from apps.crm_email.google import Gmail, ProviderError, json_request
from apps.crm_email.models import (
    Attachment,
    Authorization,
    Conversation,
    EmailTemplate,
    Mailbox,
    Message,
    WorkerHeartbeat,
)
from apps.crm_email.security import EmailError, clean_html, decrypt, encrypt
from apps.crm_email.services import build_mime, save_message
from apps.crm_email.sync import store_thread, sync_history, synchronize
from apps.crm_email.worker import run_pass, send
from apps.users.models import CustomUser

KEY = Fernet.generate_key().decode()
CONFIG = {
    "CRM_EMAIL_ENABLED": True,
    "SECRET_KEY": "test-only-email-secret-not-for-deployment-12345",
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
    "CRM_EMAIL_GOOGLE_CLIENT_ID": "test-client",
    "CRM_EMAIL_GOOGLE_CLIENT_SECRET": "test-secret",
    "CRM_EMAIL_REDIRECT_URI": "https://example.com/crm/email/callback/",
    "CRM_EMAIL_ENCRYPTION_KEYS": [KEY],
    "CRM_EMAIL_PUBSUB_TOPIC": "projects/test/topics/gmail",
    "CRM_EMAIL_PUBSUB_AUDIENCE": "https://example.com/crm/email/push/",
    "CRM_EMAIL_PUBSUB_EMAIL": "push@test.iam.gserviceaccount.com",
}


@override_settings(**CONFIG)
class EmailTests(TestCase):
    def setUp(self) -> None:
        self.owner = CustomUser.objects.create_user(
            username="owner",
            email="owner@clearcodereading.com",
            password="test-only-pass",
            role=CustomUser.Role.CRM_USER,
        )
        self.other = CustomUser.objects.create_user(
            username="other",
            email="other@clearcodereading.com",
            password="test-only-pass",
            role=CustomUser.Role.CRM_USER,
        )
        self.parent = CustomUser.objects.create_user(
            username="parent",
            email="parent@example.com",
            password="test-only-pass",
            role=CustomUser.Role.GUARDIAN,
        )
        self.lead = Lead.objects.create(
            contact_name="Test contact",
            contact_email="contact@example.com",
            school_name="Test",
        )
        self.mailbox = Mailbox.objects.create(
            user=self.owner,
            email=self.owner.email,
            google_subject="subject",
            status=Mailbox.Status.CONNECTED,
            encrypted_refresh_token=encrypt(b"private-refresh").decode(),
        )
        WorkerHeartbeat.objects.create(name="email", last_seen_at=timezone.now())
        self.client.force_login(self.owner)

    def data(self, **overrides: Any) -> dict[str, Any]:
        result = {
            "draft_id": str(uuid.uuid4()),
            "to": self.lead.contact_email,
            "cc": "",
            "bcc": "private@example.com",
            "subject": "Hello",
            "body_html": "<p>Hello <strong>there</strong></p>",
            "follow_up_days": 0,
            "action": "send",
        }
        result.update(overrides)
        return result

    def draft(self, **overrides: Any) -> Message:
        data = self.data(**overrides)
        form = ComposeForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        return save_message(
            self.owner, self.lead, form, [], str(data.get("action", "send"))
        )

    def thread(self) -> Conversation:
        return Conversation.objects.create(
            mailbox=self.mailbox, lead=self.lead, gmail_thread_id="abc", subject="Hello"
        )

    def incoming(
        self, gmail_id: str = "def", sender: str = "contact@example.com"
    ) -> dict[str, Any]:
        return {
            "id": gmail_id,
            "threadId": "abc",
            "internalDate": "1780000000000",
            "labelIds": ["INBOX"],
            "payload": {
                "mimeType": "text/html",
                "headers": [
                    {"name": "From", "value": sender},
                    {"name": "To", "value": self.owner.email},
                    {"name": "Subject", "value": "Hello"},
                    {"name": "Message-ID", "value": "<reply@example.com>"},
                ],
                "body": {
                    "data": base64.urlsafe_b64encode(
                        b'<p>Reply</p><img src="https://tracker.example/x"><script>alert(1)</script>'
                    ).decode()
                },
            },
        }

    def test_settings_and_contact_routes_render_without_credentials_exposure(
        self,
    ) -> None:
        for route, args in [
            ("crm_email_settings", []),
            ("crm_contact_email", [self.lead.pk]),
            ("crm_email_compose", [self.lead.pk]),
            ("crm_email_import", [self.lead.pk]),
            ("crm_email_template_new", []),
        ]:
            response = self.client.get(reverse(route, args=args))
            self.assertEqual(response.status_code, 200, response.content[:200])
            self.assertNotContains(response, "private-refresh")
            self.assertNotContains(response, KEY)

    def test_non_crm_user_denied(self) -> None:
        self.client.force_login(self.parent)
        self.assertEqual(
            self.client.get(reverse("crm_email_settings")).status_code, 403
        )
        self.assertEqual(
            self.client.post(reverse("crm_email_connect")).status_code, 403
        )

    def test_deleted_crm_user_denied(self) -> None:
        self.owner.is_deleted = True
        self.owner.save()
        self.assertEqual(
            self.client.get(reverse("crm_email_settings")).status_code, 403
        )

    def test_disabled_feature_has_clear_setup_state(self) -> None:
        with override_settings(CRM_EMAIL_ENABLED=False):
            response = self.client.get(reverse("crm_email_settings"))
            self.assertContains(response, "Google setup required")
            self.assertEqual(
                self.client.post(reverse("crm_email_connect")).status_code, 302
            )

    def test_recipient_and_subject_injection_rejected(self) -> None:
        for field in ["to", "cc", "bcc", "subject"]:
            form = ComposeForm(
                self.data(**{field: "hello@example.com\r\nBcc: victim@example.com"})
            )
            self.assertFalse(form.is_valid())

    def test_safe_html_removes_active_content_and_tracking(self) -> None:
        result = clean_html(
            '<p onclick="x()">Hello</p><img src="https://tracker"><script>x()</script><a href="javascript:alert(1)">link</a>'
        )
        for unsafe in ["onclick", "<script", "<img", "javascript:"]:
            self.assertNotIn(unsafe, result)

    def test_encryption_roundtrip_and_rotation(self) -> None:
        blob = encrypt(b"secret")
        self.assertNotIn(b"secret", blob)
        with override_settings(
            CRM_EMAIL_ENCRYPTION_KEYS=[Fernet.generate_key().decode(), KEY]
        ):
            self.assertEqual(decrypt(blob), b"secret")
        with (
            override_settings(
                CRM_EMAIL_ENCRYPTION_KEYS=[Fernet.generate_key().decode()]
            ),
            self.assertRaises(EmailError),
        ):
            decrypt(blob)

    def test_outbox_submission_is_idempotent(self) -> None:
        data = self.data()
        url = reverse("crm_email_compose", args=[self.lead.pk])
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(Message.objects.get().status, Message.Status.QUEUED)

    def test_draft_edit_and_queue_preserves_same_record(self) -> None:
        message = self.draft(action="draft")
        data = self.data(draft_id=str(message.pk), subject="Updated")
        self.client.post(reverse("crm_email_compose", args=[self.lead.pk]), data)
        message.refresh_from_db()
        self.assertEqual(message.subject, "Updated")
        self.assertEqual(message.status, Message.Status.QUEUED)
        self.assertEqual(Message.objects.count(), 1)

    def test_other_users_cannot_edit_or_cancel_drafts(self) -> None:
        message = self.draft(action="draft")
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(
                reverse("crm_email_compose", args=[self.lead.pk])
                + f"?draft={message.pk}"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("crm_email_compose", args=[self.lead.pk]),
                self.data(draft_id=str(message.pk)),
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("crm_email_cancel", args=[self.lead.pk, message.pk])
            ).status_code,
            404,
        )

    def test_worker_readiness_required_for_send_but_not_draft(self) -> None:
        WorkerHeartbeat.objects.all().delete()
        response = self.client.post(
            reverse("crm_email_compose", args=[self.lead.pk]), self.data()
        )
        self.assertContains(response, "worker is not ready")
        self.assertEqual(Message.objects.count(), 0)
        self.draft(action="draft")
        self.assertEqual(Message.objects.count(), 1)

    def test_unrelated_contact_cannot_be_queued(self) -> None:
        response = self.client.post(
            reverse("crm_email_compose", args=[self.lead.pk]),
            self.data(to="unrelated@example.com"),
        )
        self.assertContains(response, "Include this contact")
        self.assertFalse(Message.objects.exists())

    def test_each_user_composes_from_their_own_mailbox(self) -> None:
        other_mailbox = Mailbox.objects.create(
            user=self.other, email=self.other.email, status=Mailbox.Status.CONNECTED
        )
        for user, mailbox in ((self.owner, self.mailbox), (self.other, other_mailbox)):
            self.client.force_login(user)
            response = self.client.post(
                reverse("crm_email_compose", args=[self.lead.pk]), self.data()
            )
            self.assertEqual(response.status_code, 302)
            message = Message.objects.get(mailbox=mailbox)
            self.assertEqual(message.sender, user.email)
            self.assertEqual(message.status, Message.Status.QUEUED)

    def test_wrong_sender_is_rejected_before_gmail_send(self) -> None:
        message = self.draft()
        message.sender = self.other.email
        message.save()
        client = MagicMock(spec=Gmail)
        send(client, message)
        message.refresh_from_db()
        self.assertEqual(message.status, Message.Status.FAILED)
        client.request.assert_not_called()

    @override_settings(WEBSITE_EMAIL_FROM="hello@clearcodereading.com")
    def test_website_from_cannot_spoof_regular_crm_mail(self) -> None:
        message = self.draft()
        message.sender = "hello@clearcodereading.com"
        message.save()
        client = MagicMock(spec=Gmail)
        send(client, message)
        message.refresh_from_db()
        self.assertEqual(message.status, Message.Status.FAILED)
        client.request.assert_not_called()

    def test_disconnected_sender_is_rejected_before_gmail_send(self) -> None:
        message = self.draft()
        Mailbox.objects.filter(pk=self.mailbox.pk).update(
            status=Mailbox.Status.DISCONNECTED
        )
        client = MagicMock(spec=Gmail)
        send(client, message)
        message.refresh_from_db()
        self.assertEqual(message.status, Message.Status.FAILED)
        client.request.assert_not_called()

    def test_send_success_records_thread_and_one_follow_up(self) -> None:
        message = self.draft(follow_up_days=3)
        client = MagicMock(spec=Gmail)
        client.request.return_value = {"id": "def", "threadId": "abc"}
        send(client, message)
        message.refresh_from_db()
        self.assertEqual(message.status, Message.Status.SENT)
        assert message.conversation is not None
        self.assertEqual(message.conversation.gmail_thread_id, "abc")
        self.assertEqual(CrmActivity.objects.filter(activity_type="task").count(), 1)
        self.assertIn(b"Bcc: private@example.com", build_mime(message))

    def test_completed_send_cannot_be_sent_again(self) -> None:
        message = self.draft()
        client = MagicMock(spec=Gmail)
        client.request.return_value = {"id": "def", "threadId": "abc"}
        send(client, message)
        send(client, message)
        client.request.assert_called_once()

    def test_send_timeout_reconciles_without_resending(self) -> None:
        message = self.draft()
        client = MagicMock(spec=Gmail)
        client.request.side_effect = ProviderError()
        send(client, message)
        message.refresh_from_db()
        self.assertEqual(message.status, Message.Status.UNCERTAIN)
        client.request.side_effect = None
        client.request.return_value = {"messages": [{"id": "def", "threadId": "abc"}]}
        send(client, message)
        self.assertEqual(client.request.call_args.args[0], "GET")
        self.assertEqual(message.status, Message.Status.SENT)

    def test_missing_send_response_is_uncertain(self) -> None:
        message = self.draft()
        client = MagicMock(spec=Gmail)
        client.request.return_value = {}
        send(client, message)
        self.assertEqual(message.status, Message.Status.UNCERTAIN)

    def test_uncertain_no_match_is_never_resent(self) -> None:
        message = self.draft()
        message.status = Message.Status.UNCERTAIN
        client = MagicMock(spec=Gmail)
        client.request.return_value = {}
        send(client, message)
        self.assertEqual(message.status, Message.Status.UNCERTAIN)
        self.assertEqual(client.request.call_args.args[0], "GET")

    def test_rate_limit_requeues_and_backs_off(self) -> None:
        message = self.draft()
        client = MagicMock(spec=Gmail)
        client.request.side_effect = ProviderError(429)
        with self.assertRaises(ProviderError):
            send(client, message)
        self.assertEqual(message.status, Message.Status.QUEUED)
        assert message.next_attempt_at is not None
        self.assertGreater(message.next_attempt_at, timezone.now())

    def test_definitive_rejection_fails(self) -> None:
        message = self.draft()
        client = MagicMock(spec=Gmail)
        client.request.side_effect = ProviderError(400, False)
        send(client, message)
        self.assertEqual(message.status, Message.Status.FAILED)

    @patch("apps.crm_email.worker.Gmail")
    def test_future_scheduled_send_is_not_sent(self, provider: MagicMock) -> None:
        self.draft(scheduled_at=(timezone.now() + timedelta(days=1)).isoformat())
        self.mailbox.last_sync_at = timezone.now()
        self.mailbox.save()
        self.assertEqual(run_pass(), 0)
        provider.return_value.request.assert_not_called()

    def test_queued_message_can_be_cancelled(self) -> None:
        message = self.draft()
        response = self.client.post(
            reverse("crm_email_cancel", args=[self.lead.pk, message.pk])
        )
        self.assertEqual(response.status_code, 302)
        message.refresh_from_db()
        self.assertEqual(message.status, Message.Status.CANCELLED)

    @patch("apps.crm_email.services.revoke", return_value=False)
    @patch("apps.crm_email.services.Gmail")
    def test_disconnect_stops_future_work_even_when_google_fails(
        self, provider: MagicMock, revoke: MagicMock
    ) -> None:
        message = self.draft()
        provider.return_value.request.side_effect = ProviderError()
        response = self.client.post(
            reverse("crm_email_disconnect", args=[self.mailbox.pk]), follow=True
        )
        self.mailbox.refresh_from_db()
        message.refresh_from_db()
        self.assertEqual(self.mailbox.status, Mailbox.Status.DISCONNECTED)
        self.assertEqual(self.mailbox.encrypted_refresh_token, "")
        self.assertEqual(message.status, Message.Status.CANCELLED)
        self.assertContains(response, "revocation was not confirmed")

    @patch("apps.crm_email.google.revoke", return_value=True)
    def test_deactivated_user_connection_removed_by_worker(
        self, revoke: MagicMock
    ) -> None:
        message = self.draft()
        self.owner.is_active = False
        self.owner.save()
        run_pass()
        self.mailbox.refresh_from_db()
        message.refresh_from_db()
        self.assertEqual(self.mailbox.encrypted_refresh_token, "")
        self.assertEqual(message.status, Message.Status.CANCELLED)

    def test_import_incoming_thread_is_idempotent_and_sanitized(self) -> None:
        conversation = self.thread()
        client = MagicMock(spec=Gmail)
        client.thread.return_value = {"messages": [self.incoming()]}
        store_thread(client, conversation)
        store_thread(client, conversation)
        self.assertEqual(Message.objects.count(), 1)
        self.assertNotIn("<img", Message.objects.get().body_html)
        self.assertNotIn("<script", Message.objects.get().body_html)

    def test_import_rejects_unrelated_thread_before_publishing(self) -> None:
        conversation = self.thread()
        conversation.import_pending = True
        conversation.save()
        client = MagicMock(spec=Gmail)
        client.thread.return_value = {
            "messages": [self.incoming(sender="stranger@example.com")]
        }
        with self.assertRaises(EmailError):
            store_thread(client, conversation)
        self.assertFalse(Message.objects.exists())

    def test_only_linked_threads_are_fetched_from_history(self) -> None:
        conversation = self.thread()
        self.mailbox.history_id = "1"
        client = MagicMock(spec=Gmail)
        client.request.return_value = {
            "historyId": "3",
            "history": [
                {
                    "messagesAdded": [
                        {"message": {"threadId": "abc"}},
                        {"message": {"threadId": "aaa"}},
                    ]
                }
            ],
        }
        client.thread.return_value = {"messages": [self.incoming()]}
        sync_history(client, self.mailbox)
        client.thread.assert_called_once_with(conversation.gmail_thread_id)
        self.assertEqual(self.mailbox.history_id, "3")

    def test_expired_history_cursor_recovers(self) -> None:
        self.mailbox.history_id = "1"
        self.mailbox.recovery_cursor = 100
        client = MagicMock(spec=Gmail)
        client.request.side_effect = [ProviderError(404, False), {"historyId": "300"}]
        sync_history(client, self.mailbox)
        self.assertEqual(self.mailbox.history_id, "300")
        self.assertEqual(self.mailbox.recovery_cursor, 0)

    def test_linked_history_shared_but_bcc_private(self) -> None:
        message = self.draft()
        message.status = Message.Status.SENT
        message.conversation = self.thread()
        message.save()
        self.client.force_login(self.other)
        response = self.client.get(
            reverse("crm_email_thread", args=[self.lead.pk, message.conversation_id])
        )
        self.assertContains(response, "Hello")
        self.assertNotContains(response, "private@example.com")
        self.assertNotContains(response, "Reply all")

    def test_attachment_download_authorization(self) -> None:
        message = self.draft(action="draft")
        attachment = Attachment.objects.create(
            message=message,
            filename="hello.txt",
            size=5,
            encrypted_data=encrypt(b"hello"),
        )
        url = reverse("crm_email_download", args=[attachment.pk])
        self.assertEqual(self.client.get(url).content, b"hello")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)
        message.status = Message.Status.SENT
        message.conversation = self.thread()
        message.save()
        response = self.client.get(url)
        self.assertEqual(response.content, b"hello")
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", response["Content-Disposition"])
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_attachment_upload_encrypted_and_size_limited(self) -> None:
        form = ComposeForm(self.data())
        self.assertTrue(form.is_valid())
        upload = SimpleUploadedFile("hello.txt", b"hello")
        message = save_message(self.owner, self.lead, form, [upload], "draft")
        attachment = message.attachments.get()
        self.assertEqual(decrypt(bytes(attachment.encrypted_data)), b"hello")
        with override_settings(CRM_EMAIL_ATTACHMENT_LIMIT=1):
            form = ComposeForm(self.data())
            self.assertTrue(form.is_valid())
            with self.assertRaises(EmailError):
                save_message(
                    self.owner,
                    self.lead,
                    form,
                    [SimpleUploadedFile("hello.txt", b"hello")],
                    "draft",
                )

    def test_reply_all_excludes_own_address_and_never_includes_bcc(self) -> None:
        message = Message.objects.create(
            mailbox=self.mailbox,
            lead=self.lead,
            conversation=self.thread(),
            status=Message.Status.RECEIVED,
            sender=self.lead.contact_email,
            to=[self.owner.email],
            cc=["colleague@example.com"],
            bcc=["secret@example.com"],
            subject="Hello",
            rfc_message_id="<reply@example.com>",
        )
        response = self.client.get(
            reverse("crm_email_compose", args=[self.lead.pk])
            + f"?reply={message.pk}&all=1"
        )
        form = response.context["form"]
        self.assertEqual(form.initial["to"], self.lead.contact_email)
        self.assertEqual(form.initial["cc"], "colleague@example.com")
        self.assertNotIn("bcc", form.initial)
        draft = self.draft(reply_id=str(message.pk), subject="Changed")
        self.assertEqual(draft.subject, message.subject)
        self.assertIn(b"In-Reply-To: <reply@example.com>", build_mime(draft))

    def test_templates_private_and_sanitized(self) -> None:
        response = self.client.post(
            reverse("crm_email_template_new"),
            {
                "name": "Intro",
                "subject": "Hello",
                "body_html": '<p>Intro</p><img src="x">',
            },
        )
        self.assertEqual(response.status_code, 302)
        template = EmailTemplate.objects.get()
        self.assertNotIn("<img", template.body_html)
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(
                reverse("crm_email_template", args=[template.pk])
            ).status_code,
            404,
        )

    @patch("apps.crm_email.views.Gmail")
    def test_historical_import_requires_explicit_signed_selection(
        self, provider: MagicMock
    ) -> None:
        url = reverse("crm_email_import", args=[self.lead.pk])
        provider.return_value.request.side_effect = [
            {"threads": [{"id": "abc"}]},
            {"messages": [self.incoming()]},
        ]
        response = self.client.post(url)
        self.assertFalse(Conversation.objects.exists())
        token = response.context["candidates"][0]["selection"]
        self.client.post(url, {"selection": token})
        self.assertTrue(Conversation.objects.get().import_pending)
        self.assertFalse(Message.objects.exists())

    @patch("apps.crm_email.views.id_token.verify_oauth2_token")
    def test_authenticated_push_is_durable_and_duplicate_safe(
        self, verify: MagicMock
    ) -> None:
        verify.return_value = {
            "email": CONFIG["CRM_EMAIL_PUBSUB_EMAIL"],
            "email_verified": True,
        }
        payload = {
            "message": {
                "data": base64.b64encode(
                    json.dumps(
                        {"emailAddress": self.owner.email, "historyId": "123"}
                    ).encode()
                ).decode()
            }
        }
        for _ in range(2):
            response = self.client.post(
                reverse("crm_email_push"),
                json.dumps(payload),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test",
            )
            self.assertEqual(response.status_code, 204)
        self.mailbox.refresh_from_db()
        self.assertIsNotNone(self.mailbox.sync_requested_at)
        self.assertFalse(Message.objects.exists())
        verify.assert_called_with(
            "test", AnyGoogleRequest(), CONFIG["CRM_EMAIL_PUBSUB_AUDIENCE"]
        )

    @patch("apps.crm_email.views.id_token.verify_oauth2_token")
    def test_forged_push_denied(self, verify: MagicMock) -> None:
        verify.return_value = {"email": "attacker@example.com", "email_verified": True}
        self.assertEqual(
            self.client.post(
                reverse("crm_email_push"),
                "{}",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer bad",
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("crm_email_push"), "{}", content_type="application/json"
            ).status_code,
            403,
        )

    def test_gmail_drafts_never_enter_shared_history(self) -> None:
        conversation = self.thread()
        client = MagicMock(spec=Gmail)
        private_draft = self.incoming("aaa")
        private_draft["labelIds"] = ["DRAFT"]
        client.thread.return_value = {"messages": [self.incoming(), private_draft]}
        store_thread(client, conversation)
        self.assertEqual(Message.objects.count(), 1)
        self.assertFalse(Message.objects.filter(gmail_id="aaa").exists())

    def test_disabled_default_signing_secret_blocks_activation(self) -> None:
        from apps.crm_email.security import configuration_errors

        with override_settings(SECRET_KEY="dev-only-change-me"):
            self.assertTrue(
                any("DJANGO_SECRET_KEY" in value for value in configuration_errors())
            )

    def test_daily_watch_renewal_and_push_during_sync_preserved(self) -> None:
        self.mailbox.history_id = "100"
        self.mailbox.sync_requested_at = timezone.now()
        self.mailbox.save()
        client = MagicMock(spec=Gmail)

        def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            if path == "history":
                Mailbox.objects.filter(pk=self.mailbox.pk).update(
                    sync_requested_at=timezone.now() + timedelta(seconds=1)
                )
                return {"historyId": "101"}
            return {
                "expiration": str(
                    int((timezone.now() + timedelta(days=7)).timestamp() * 1000)
                )
            }

        client.request.side_effect = request
        synchronize(client, self.mailbox)
        self.mailbox.refresh_from_db()
        self.assertIsNotNone(self.mailbox.watch_renewed_at)
        self.assertIsNotNone(self.mailbox.sync_requested_at)
        self.assertEqual(self.mailbox.history_id, "101")

    def test_oauth_state_is_bound_to_user_and_single_use(self) -> None:
        response = self.client.post(reverse("crm_email_connect"))
        params = parse_qs(urlparse(response["Location"]).query)
        self.assertIn("code_challenge", params)
        self.assertEqual(params["access_type"], ["offline"])
        state = params["state"][0]
        self.client.get(
            reverse("crm_email_callback"), {"state": state, "error": "access_denied"}
        )
        self.assertTrue(Authorization.objects.get().consumed)
        with patch("apps.crm_email.google.json_request") as provider:
            self.client.get(
                reverse("crm_email_callback"), {"state": state, "code": "test"}
            )
            provider.assert_not_called()

    @patch("apps.crm_email.google.json_request")
    @patch("apps.crm_email.google.id_token.verify_oauth2_token")
    def test_oauth_verified_identity_and_offline_token(
        self, verify: MagicMock, provider: MagicMock
    ) -> None:
        response = self.client.post(reverse("crm_email_connect"))
        state = parse_qs(urlparse(response["Location"]).query)["state"][0]
        verify.return_value = {
            "nonce": Authorization.objects.get().nonce,
            "hd": "clearcodereading.com",
            "email_verified": True,
            "email": self.owner.email,
            "sub": "subject",
        }
        provider.side_effect = [
            {
                "id_token": "signed",
                "access_token": "access",
                "refresh_token": "fresh",
                "scope": "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly",
            },
            {"emailAddress": self.owner.email},
        ]
        self.client.get(reverse("crm_email_callback"), {"state": state, "code": "test"})
        self.mailbox.refresh_from_db()
        self.assertEqual(
            decrypt(self.mailbox.encrypted_refresh_token.encode()), b"fresh"
        )

    @patch("apps.crm_email.google.json_request")
    @patch("apps.crm_email.google.id_token.verify_oauth2_token")
    def test_wrong_google_domain_rejected(
        self, verify: MagicMock, provider: MagicMock
    ) -> None:
        response = self.client.post(reverse("crm_email_connect"))
        state = parse_qs(urlparse(response["Location"]).query)["state"][0]
        verify.return_value = {
            "nonce": Authorization.objects.get().nonce,
            "hd": "wrong.com",
            "email_verified": True,
            "email": self.owner.email,
            "sub": "subject",
        }
        provider.return_value = {"id_token": "signed"}
        response = self.client.get(
            reverse("crm_email_callback"), {"state": state, "code": "test"}, follow=True
        )
        self.assertContains(response, "Choose your own")
        self.mailbox.refresh_from_db()
        self.assertEqual(
            decrypt(self.mailbox.encrypted_refresh_token.encode()), b"private-refresh"
        )

    @patch("apps.crm_email.google.json_request", side_effect=ProviderError(400, False))
    def test_refresh_revocation_marks_reconnect(self, provider: MagicMock) -> None:
        with self.assertRaises(EmailError):
            Gmail(self.mailbox).request("GET", "profile")
        self.mailbox.refresh_from_db()
        self.assertEqual(self.mailbox.status, Mailbox.Status.RECONNECT)

    @patch("apps.crm_email.google.requests.request")
    def test_malformed_provider_response_is_safe_error(
        self, request: MagicMock
    ) -> None:
        request.return_value.status_code = 200
        request.return_value.content = b"not-json"
        request.return_value.json.side_effect = ValueError("private provider data")
        with self.assertRaises(ProviderError) as caught:
            json_request("GET", "https://gmail.googleapis.com/")
        self.assertNotIn("private provider data", str(caught.exception))


class AnyGoogleRequest:
    def __eq__(self, other: object) -> bool:
        from google.auth.transport.requests import Request

        return isinstance(other, Request)
