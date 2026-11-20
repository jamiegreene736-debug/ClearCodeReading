import base64
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.notifications.board_sms import RELAY_NUMBER, drain_board_texts
from apps.users.models import (
    BoardTextAccount,
    BoardTextDelivery,
    ChildProfile,
    CustomUser,
    GuardianRelationship,
    ReaderBoardPost,
)

SIGNING_KEY = b"example-signing-key-for-tests"
PARENT = "+12025550101"
TEACHER = "+12025550102"


@override_settings(
    QUO_API_KEY="mock-key",
    QUO_WEBHOOK_KEY="whsec_" + base64.b64encode(SIGNING_KEY).decode(),
)
class BoardTextTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.parent = CustomUser.objects.create_user(username="parent", role="guardian")
        cls.teacher = CustomUser.objects.create_user(
            username="teacher", email="teacher@example.test", role="teacher"
        )
        cls.child = ChildProfile.objects.create(
            first_name="Robin", learning_profile={"assigned_teacher_id": cls.teacher.pk}
        )
        GuardianRelationship.objects.create(
            guardian=cls.parent,
            child=cls.child,
            relationship_type="parent",
            consent_status="granted",
            is_primary=True,
        )
        cls.board = reverse("portal_reader_board", args=[cls.child.pk])
        cls.text_settings = reverse("portal_board_text_settings", args=[cls.child.pk])

    def inbound(
        self,
        sender: str,
        body: str,
        *,
        number: str = RELAY_NUMBER,
        event_id: str = "",
        signed: bool = True,
    ) -> int:
        event_id = event_id or str(uuid4())
        event = {
            "type": "message.received",
            "data": {
                "resource": {"id": event_id, "direction": "incoming", "text": body},
                "context": {
                    "senderIdentifier": sender,
                    "recipientIdentifiers": [number],
                },
            },
        }
        raw = json.dumps(event).encode()
        stamp = str(int(time.time()))
        sig = base64.b64encode(
            hmac.digest(SIGNING_KEY, f"{event_id}.{stamp}.".encode() + raw, "sha256")
        ).decode()
        return self.client.post(
            reverse("quo_board_webhook"),
            raw,
            content_type="application/json",
            HTTP_WEBHOOK_ID=event_id,
            HTTP_WEBHOOK_TIMESTAMP=stamp,
            HTTP_WEBHOOK_SIGNATURE="v1," + (sig if signed else "wrong"),
        ).status_code

    def invite(self, user: CustomUser) -> str:
        self.client.force_login(user)
        self.assertEqual(
            self.client.post(self.text_settings, {"action": "enable"}).status_code, 302
        )
        return "JOIN " + self.client.session["reader_sms_join"]

    def connect(self, user: CustomUser, number: str) -> None:
        self.assertEqual(self.inbound(number, self.invite(user)), 204)
        self.assertEqual(BoardTextAccount.objects.get(user=user).phone, number)

    def post_as_parent(self) -> None:
        self.client.force_login(self.parent)
        self.assertEqual(
            self.client.post(
                self.board, {"body": "We read together.", "submission_id": str(uuid4())}
            ).status_code,
            302,
        )

    @patch("apps.notifications.board_sms.requests.post")
    def test_relay_for_portal_note_and_signed_reply(self, send: Mock) -> None:
        self.connect(self.parent, PARENT)
        self.connect(self.teacher, TEACHER)
        self.post_as_parent()
        send.return_value.status_code = 202
        send.return_value.json.return_value = {"data": {"id": "provider-receipt"}}
        drain_board_texts()
        payload = send.call_args.kwargs["json"]
        self.assertEqual(payload["from"], RELAY_NUMBER)
        self.assertEqual(payload["to"], [TEACHER])
        self.assertIn("We read together.", payload["content"])
        self.assertNotIn(PARENT, payload["content"])
        reply = f"R{self.child.pk} Wonderful work!"
        for _ in range(2):
            self.assertEqual(self.inbound(TEACHER, reply, event_id="incoming-1"), 204)
        self.assertEqual(
            ReaderBoardPost.objects.get(author=self.teacher).body, "Wonderful work!"
        )
        drain_board_texts()
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args.kwargs["json"]["to"], [PARENT])
        self.assertEqual(BoardTextDelivery.objects.filter(status="accepted").count(), 2)

    def test_requires_signed_callback_correct_relay_and_authorized_board(self) -> None:
        self.connect(self.parent, PARENT)
        unrelated = ChildProfile.objects.create(first_name="Other")
        self.assertEqual(self.inbound(PARENT, "STOP", signed=False), 403)
        self.inbound(PARENT, "STOP", number="+12025550109")
        self.inbound(PARENT, "A reply without a board code")
        self.inbound(PARENT, f"R{unrelated.pk} Wrong board")
        self.inbound("+12025550109", f"R{self.child.pk} Unknown sender")
        self.assertFalse(ReaderBoardPost.objects.exists())
        self.assertTrue(BoardTextAccount.objects.filter(phone=PARENT).exists())

    @patch("apps.notifications.board_sms.requests.post")
    def test_stop_and_removed_teacher_cancel_queued_delivery(self, send: Mock) -> None:
        self.connect(self.teacher, TEACHER)
        self.post_as_parent()
        self.inbound(TEACHER, "STOP")
        self.assertFalse(BoardTextAccount.objects.filter(user=self.teacher).exists())
        drain_board_texts()
        self.connect(self.teacher, TEACHER)
        self.post_as_parent()
        self.child.learning_profile = {}
        self.child.save()
        drain_board_texts()
        self.assertEqual(
            BoardTextDelivery.objects.filter(status="cancelled").count(), 2
        )
        send.assert_not_called()

    def test_expired_codes_and_numbers_already_in_use(self) -> None:
        self.connect(self.parent, PARENT)
        invitation = self.invite(self.teacher)
        self.inbound(PARENT, invitation)
        self.assertIsNone(BoardTextAccount.objects.get(user=self.teacher).phone)
        BoardTextAccount.objects.filter(user=self.teacher).update(
            code_expires_at=timezone.now() - timedelta(minutes=1)
        )
        self.inbound(TEACHER, invitation)
        self.assertIsNone(BoardTextAccount.objects.get(user=self.teacher).verified_at)

    @patch("apps.notifications.board_sms.requests.post")
    def test_rate_limit_retries_and_ambiguous_timeout_stops(self, send: Mock) -> None:
        self.connect(self.teacher, TEACHER)
        self.post_as_parent()
        send.return_value.status_code = 429
        send.return_value.headers = {"Retry-After": "90"}
        drain_board_texts()
        delivery = BoardTextDelivery.objects.get()
        self.assertEqual(delivery.status, "queued")
        self.assertGreater(
            delivery.next_attempt, timezone.now() + timedelta(seconds=88)
        )
        BoardTextDelivery.objects.update(next_attempt=timezone.now())
        send.side_effect = requests.Timeout
        drain_board_texts()
        drain_board_texts()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, BoardTextDelivery.Status.UNCERTAIN)
        self.assertEqual(send.call_count, 2)
