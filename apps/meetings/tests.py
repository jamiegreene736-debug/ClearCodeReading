import base64
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.crm_email.google import ProviderError
from apps.crm_email.models import BETHANY_EMAIL, Mailbox
from apps.meetings.mail import process_due_notices
from apps.meetings.models import FamilyMeeting, MeetingNotice
from apps.meetings.services import save_meeting
from apps.schools.models import School, SchoolMembership
from apps.users.models import ChildProfile, CustomUser, GuardianRelationship


class MeetingTests(TestCase):
    @staticmethod
    def user(name: str, role: str) -> CustomUser:
        return CustomUser.objects.create_user(
            username=name, email=f"{name}@example.org", role=role
        )

    def setUp(self) -> None:
        self.teacher = self.user("teach", "teacher")
        self.parent = self.user("parent", "guardian")
        self.child = ChildProfile.objects.create(
            first_name="Reader",
            learning_profile={"assigned_teacher_id": self.teacher.pk},
        )
        self.family = GuardianRelationship.objects.create(
            guardian=self.parent,
            child=self.child,
            relationship_type="parent",
            consent_status="granted",
        )
        self.start = (timezone.now() + timedelta(days=5)).replace(
            second=0, microsecond=0
        )
        self.end = self.start + timedelta(minutes=30)
        self.client.force_login(self.teacher)

    def book(self) -> FamilyMeeting:
        return save_meeting(
            self.teacher, self.family.pk, self.start, self.end, "Room 211"
        )

    def test_teaching_admin_can_schedule_for_assigned_families(self) -> None:
        self.teacher.role = CustomUser.Role.SUPER_ADMIN
        self.teacher.save()
        self.assertEqual(self.client.get(reverse("new_family_meeting")).status_code, 403)
        self.teacher.profile.teaches_families = True
        self.teacher.profile.save()
        self.assertContains(self.client.get(reverse("new_family_meeting")), self.parent.email)
        self.assertEqual(self.book().teacher_id, self.teacher.pk)

    def test_normal_entry_points_and_email_lifecycle(self) -> None:
        self.assertContains(
            self.client.get(reverse("portal_dashboard")), reverse("family_meetings")
        )
        form = self.client.get(reverse("new_family_meeting"))
        self.assertContains(form, self.parent.email)
        data = {
            "family": self.family.pk,
            "starts_at": timezone.localtime(self.start).strftime("%Y-%m-%dT%H:%M"),
            "ends_at": timezone.localtime(self.end).strftime("%Y-%m-%dT%H:%M"),
            "location": "Room 211",
        }
        self.assertRedirects(
            self.client.post(reverse("new_family_meeting"), data),
            reverse("family_meetings"),
        )
        meeting = FamilyMeeting.objects.get()
        self.assertEqual(meeting.starts_at, self.start)
        self.assertEqual(
            meeting.notices.get(kind="reminder").due_at, self.start - timedelta(days=1)
        )
        edit = reverse("edit_family_meeting", args=[meeting.pk])
        self.assertContains(self.client.get(edit), "Room 211")
        self.client.force_login(self.parent)
        self.assertContains(self.client.get(reverse("family_meetings")), "Room 211")
        self.assertEqual(self.client.post(edit, data).status_code, 403)
        self.client.force_login(self.teacher)
        data.update(location="https://example.org/room", revision=1)
        self.assertRedirects(self.client.post(edit, data), reverse("family_meetings"))
        self.assertEqual(meeting.notices.filter(revision=1, state="skipped").count(), 2)
        self.assertContains(self.client.post(edit, data), "This meeting has changed")
        self.assertEqual(meeting.notices.filter(kind="update").count(), 1)
        cancel = reverse("cancel_family_meeting", args=[meeting.pk])
        self.assertEqual(self.client.get(cancel).status_code, 405)
        self.assertRedirects(
            self.client.post(cancel, {"revision": 2}), reverse("family_meetings")
        )
        self.assertEqual(
            list(
                meeting.notices.filter(state="pending").values_list("kind", flat=True)
            ),
            ["cancel"],
        )
        self.client.force_login(self.parent)
        self.assertNotContains(
            self.client.get(reverse("family_meetings")), "example.org/room"
        )
        self.assertContains(
            self.client.get(reverse("family_meetings"), {"view": "history"}),
            "example.org/room",
        )

    def test_access_is_limited_to_invitee_assigned_teacher_and_school_admin(
        self,
    ) -> None:
        school = School(schema_name="meeting_scope", name="One", slug="meeting-scope")
        school.auto_create_schema = False
        school.save()
        self.child.school = school
        self.child.save()
        meeting = self.book()
        other_parent = self.user("second", "guardian")
        GuardianRelationship.objects.create(
            child=self.child, guardian=other_parent, consent_status="granted"
        )
        for user in [
            other_parent,
            self.user("other", "teacher"),
            self.user("school", "school_admin"),
        ]:
            self.client.force_login(user)
            self.assertNotContains(
                self.client.get(reverse("family_meetings")), "Room 211"
            )
            if user.role == "school_admin":
                SchoolMembership.objects.create(school=school, user=user, role="admin")
                self.assertContains(
                    self.client.get(reverse("family_meetings")), "Room 211"
                )
            if user.role == "teacher":
                self.assertEqual(
                    self.client.get(
                        reverse("edit_family_meeting", args=[meeting.pk])
                    ).status_code,
                    404,
                )
                response = self.client.post(
                    reverse("new_family_meeting"), {"family": self.family.pk}
                )
                self.assertContains(response, "You need an assigned reader")
                self.assertEqual(FamilyMeeting.objects.count(), 1)
        self.client.force_login(self.user("student", "student"))
        self.assertEqual(self.client.get(reverse("family_meetings")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("family_meetings")).status_code, 302)

    def test_overlap_validation_and_short_notice(self) -> None:
        self.book()
        with self.assertRaisesMessage(ValidationError, "already has"):
            self.book()
        other = self.user("second-teacher", "teacher")
        child = ChildProfile.objects.create(
            first_name="Sibling",
            learning_profile={"assigned_teacher_id": str(other.pk)},
        )
        family = GuardianRelationship.objects.create(
            guardian=self.parent, child=child, consent_status="granted"
        )
        with self.assertRaisesMessage(ValidationError, "already has"):
            save_meeting(other, family.pk, self.start, self.end, "Room 222")
        adjacent = save_meeting(
            other, family.pk, self.end, self.end + timedelta(hours=1), "Room 222"
        )
        self.assertEqual(adjacent.notices.count(), 2)
        for start, end in [
            (self.end, self.start),
            (timezone.now() - timedelta(hours=1), timezone.now()),
        ]:
            with self.assertRaises(ValidationError):
                save_meeting(self.teacher, self.family.pk, start, end, "Room 222")
        soon = timezone.now() + timedelta(hours=3)
        meeting = save_meeting(
            self.teacher, self.family.pk, soon, soon + timedelta(hours=1), "Office"
        )
        self.assertEqual(
            list(meeting.notices.values_list("kind", flat=True)), ["invite"]
        )

    def test_revocation_blocks_parent_view_and_pending_email(self) -> None:
        meeting = self.book()
        self.family.consent_status = "revoked"
        self.family.save()
        self.client.force_login(self.parent)
        self.assertNotContains(self.client.get(reverse("family_meetings")), "Room 211")
        from apps.meetings.mail import claim

        self.assertIsNone(claim(meeting.notices.get(kind="invite").pk))
        self.assertEqual(meeting.notices.get(kind="invite").state, "skipped")

    def setup_mailbox(self) -> None:
        sender = CustomUser.objects.create_user(
            username="sender", email=BETHANY_EMAIL, role="super_admin"
        )
        Mailbox.objects.create(
            user=sender, email=BETHANY_EMAIL, status=Mailbox.Status.CONNECTED
        )

    @patch("apps.meetings.mail.Gmail")
    def test_worker_mails_current_details_once_and_reminder_when_due(
        self, gmail: MagicMock
    ) -> None:
        self.setup_mailbox()
        meeting = self.book()
        gmail.return_value.request.return_value = {"id": "abcd1234"}
        process_due_notices()
        process_due_notices()
        gmail.return_value.request.assert_called_once()
        raw = gmail.return_value.request.call_args.kwargs["json"]["raw"]
        email = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(raw)
        )
        self.assertEqual(email["To"], self.parent.email)
        part = email.get_body()
        assert part is not None
        body = part.get_content()
        for item in (
            "Room 211",
            str(self.teacher),
            timezone.localtime(self.start).strftime("%I:%M %p %Z"),
            "/portal/meetings/",
        ):
            self.assertIn(item, body)
        meeting.notices.filter(kind="reminder").update(due_at=timezone.now())
        process_due_notices()
        self.assertEqual(meeting.notices.filter(state="sent").count(), 2)

    @patch("apps.meetings.mail.Gmail")
    def test_uncertain_delivery_reconciles_without_second_send(
        self, gmail: MagicMock
    ) -> None:
        self.setup_mailbox()
        notice = self.book().notices.get(kind="invite")
        provider = gmail.return_value.request
        provider.side_effect = [
            ProviderError(),
            {"messages": 42},
            {"messages": [{"id": "beef1234"}]},
        ]
        process_due_notices()
        notice.refresh_from_db()
        self.assertEqual(notice.state, "checking")
        MeetingNotice.objects.filter(pk=notice.pk).update(due_at=timezone.now())
        process_due_notices()
        notice.refresh_from_db()
        self.assertEqual(notice.state, "checking")
        MeetingNotice.objects.filter(pk=notice.pk).update(due_at=timezone.now())
        process_due_notices()
        self.assertEqual(
            [call.args[0] for call in provider.call_args_list], ["POST", "GET", "GET"]
        )
        notice.refresh_from_db()
        self.assertEqual(notice.state, "sent")

    @patch("apps.meetings.mail.Gmail")
    def test_rate_limit_defers_and_auth_failure_requires_review(
        self, gmail: MagicMock
    ) -> None:
        self.setup_mailbox()
        notice = self.book().notices.get(kind="invite")
        gmail.return_value.request.side_effect = [
            ProviderError(429),
            ProviderError(401, False),
        ]
        process_due_notices()
        notice.refresh_from_db()
        self.assertEqual(notice.state, "pending")
        self.assertGreater(notice.due_at, timezone.now())
        MeetingNotice.objects.filter(pk=notice.pk).update(due_at=timezone.now())
        process_due_notices()
        notice.refresh_from_db()
        self.assertEqual(notice.state, "review")

    def test_admin_back_office_is_read_only(self) -> None:
        self.book()
        admin = CustomUser.objects.create_superuser(
            username="backoffice", email="office@example.org", password=None
        )
        self.client.force_login(admin)
        page = self.client.get(reverse("admin:meetings_familymeeting_changelist"))
        self.assertContains(page, "Room 211")
        self.assertEqual(
            self.client.get(reverse("admin:meetings_familymeeting_add")).status_code,
            403,
        )

    def test_local_form_rejects_dst_gap_and_ambiguous_hour(self) -> None:
        for clock in ["2030-03-10T02:15", "2030-11-03T01:30"]:
            with self.subTest(clock=clock):
                response = self.client.post(
                    reverse("new_family_meeting"),
                    {
                        "family": self.family.pk,
                        "starts_at": clock,
                        "ends_at": clock,
                        "location": "Office",
                    },
                )
                self.assertIn(
                    "ambiguous", response.context["form"].errors["starts_at"][0]
                )
                self.assertFalse(FamilyMeeting.objects.exists())

    @patch("apps.meetings.mail.Gmail")
    def test_missing_acceptance_reconciles_and_stale_reminder_is_skipped(
        self, gmail: MagicMock
    ) -> None:
        self.setup_mailbox()
        meeting = self.book()
        gmail.return_value.request.return_value = {}
        process_due_notices()
        self.assertEqual(meeting.notices.get(kind="invite").state, "checking")
        FamilyMeeting.objects.filter(pk=meeting.pk).update(
            starts_at=timezone.now() - timedelta(hours=2)
        )
        meeting.notices.filter(kind="reminder").update(due_at=timezone.now())
        process_due_notices()
        self.assertEqual(meeting.notices.get(kind="reminder").state, "skipped")
        gmail.return_value.request.assert_called_once()
