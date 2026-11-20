from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.users.models import (
    ChildProfile,
    CustomUser,
    GuardianRelationship,
    ReaderBoardNotification,
    ReaderBoardPost,
)


class ReaderBoardTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.parent = CustomUser.objects.create_user(
            username="family", email="family@example.com", role="guardian"
        )
        cls.teacher = CustomUser.objects.create_user(
            username="teacher",
            email="teacher@example.com",
            role="teacher",
            first_name="Taylor",
        )
        cls.other_parent = CustomUser.objects.create_user(
            username="other", email="other@example.com", role="guardian"
        )
        cls.other_teacher = CustomUser.objects.create_user(
            username="otherteacher", email="otherteacher@example.com", role="teacher"
        )
        cls.reader = ChildProfile.objects.create(
            first_name="Avery", learning_profile={"assigned_teacher_id": cls.teacher.pk}
        )
        cls.relationship = GuardianRelationship.objects.create(
            guardian=cls.parent,
            child=cls.reader,
            is_primary=True,
            relationship_type="parent",
            consent_status="granted",
        )
        cls.board = reverse("portal_reader_board", args=[cls.reader.pk])
        cls.notifications = reverse("portal_board_notifications")

    def test_teacher_resources_parent_reply_and_unread_notifications(self) -> None:
        self.client.force_login(self.teacher)
        note = {
            "submission_id": str(uuid4()),
            "body": "Try this story together.",
            "resource_url": "https://example.test/story",
        }
        self.client.post(self.board, note)
        self.client.post(self.board, note)
        self.assertEqual(ReaderBoardPost.objects.count(), 1)
        self.assertEqual(ReaderBoardNotification.objects.count(), 1)
        self.client.force_login(self.parent)
        dashboard = self.client.get(reverse("portal_dashboard"))
        self.assertContains(dashboard, "This week’s resources")
        self.assertContains(dashboard, "Try this story together.")
        self.assertContains(dashboard, "1 unread")
        self.assertEqual(
            self.client.get(self.notifications).json()["unread"],
            {str(self.reader.pk): 1},
        )
        page = self.client.get(self.board)
        self.assertContains(page, "<time datetime=")
        self.assertEqual(self.client.get(self.notifications).json()["unread"], {})
        self.client.post(
            self.board,
            {
                "submission_id": str(uuid4()),
                "body": "Avery loved it.",
                "resource_url": "https://example.test/ignored",
            },
        )
        self.assertEqual(
            ReaderBoardPost.objects.get(author=self.parent).resource_url, ""
        )
        self.client.force_login(self.teacher)
        self.assertEqual(
            self.client.get(self.notifications).json()["unread"],
            {str(self.reader.pk): 1},
        )
        self.assertContains(self.client.get(self.board), "Avery loved it.")

    def test_teaching_admin_can_exchange_notes_with_assigned_family(self) -> None:
        self.teacher.role = CustomUser.Role.SUPER_ADMIN
        self.teacher.save()
        self.teacher.profile.teaches_families = True
        self.teacher.profile.save()
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(reverse("portal_dashboard")), self.board)
        self.client.post(self.board, {"submission_id": str(uuid4()), "body": "Today's practice"})
        self.client.force_login(self.parent)
        self.assertContains(self.client.get(reverse("portal_dashboard")), "Today&#x27;s practice")
        self.client.post(self.board, {"submission_id": str(uuid4()), "body": "Thank you"})
        self.assertTrue(ReaderBoardNotification.objects.filter(recipient=self.teacher).exists())

    def test_private_notes_and_counts_require_current_access(self) -> None:
        self.client.force_login(self.teacher)
        self.client.post(
            self.board, {"submission_id": str(uuid4()), "body": "Private practice note"}
        )
        for outsider in (self.other_parent, self.other_teacher):
            self.client.force_login(outsider)
            self.assertEqual(self.client.get(self.board).status_code, 404)
            self.assertEqual(
                self.client.post(self.board, {"body": "Hello"}).status_code, 404
            )
            self.assertEqual(self.client.get(self.notifications).json()["unread"], {})
            self.assertNotContains(
                self.client.get(reverse("portal_dashboard")), "Private practice note"
            )
        self.relationship.consent_expires_at = timezone.now() - timedelta(days=1)
        self.relationship.save()
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(self.board).status_code, 404)
        self.assertEqual(self.client.get(self.notifications).json()["unread"], {})
        self.reader.learning_profile = {"assigned_teacher_id": self.other_teacher.pk}
        self.reader.save()
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(self.board).status_code, 404)

    def test_parent_cannot_send_to_unassigned_teacher(self) -> None:
        self.reader.learning_profile = {}
        self.reader.save()
        self.client.force_login(self.parent)
        self.assertContains(
            self.client.post(
                self.board, {"body": "A question", "submission_id": str(uuid4())}
            ),
            "Posting opens",
        )
        self.assertFalse(ReaderBoardPost.objects.exists())

    def test_rollover_keeps_last_weeks_resources_out_of_this_week(self) -> None:
        self.client.force_login(self.teacher)
        self.client.post(
            self.board,
            {
                "submission_id": str(uuid4()),
                "body": "Last week’s sound game",
                "resource_url": "https://example.test/sounds",
            },
        )
        self.client.force_login(self.parent)
        url = reverse("portal_dashboard")
        first = self.client.get(url)
        previous = first.context["current_week"]
        original_localdate = timezone.localdate
        next_monday = previous["week"] + timedelta(days=7)
        with patch(
            "apps.users.weekly_resources.timezone.localdate",
            side_effect=lambda value=None, *args, **kwargs: (
                original_localdate(value, *args, **kwargs) if value else next_monday
            ),
        ):
            second = self.client.get(url)
            current = second.context["current_week"]
            earlier = second.context["earlier_weeks"][0]
            self.assertEqual(current["week"], next_monday)
            self.assertFalse(current["teacher_links"])
            self.assertEqual(earlier["week"], previous["week"])
            self.assertEqual(earlier["activity_title"], previous["activity_title"])
            self.assertEqual(
                [post.body for post in earlier["teacher_links"]],
                ["Last week’s sound game"],
            )
            self.client.force_login(self.other_parent)
            self.assertNotContains(self.client.get(url), "Last week’s sound game")
