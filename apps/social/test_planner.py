from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.social.exceptions import SocialError
from apps.social.models import ContentPlan, ContentWeek, SocialPost
from apps.social.planner import (
    _context,
    _generate_week,
    _prepare_weeks,
    _run_locked,
    get_plan,
    maintain_content_plan,
    request_preview,
    save_plan,
    skip_week,
    upcoming_slots,
)
from apps.social.planner_ai import generate_content, structured, validate_content
from apps.social.services import EASTERN, cancel_schedule, publish_due
from apps.users.models import CustomUser

CONTENT = {
    "title": "Make room for a quiet reading moment",
    "brief": "Encourage a small, calm family reading routine.",
    "why": "An achievable routine helps families make time for reading together.",
    "facebook": "Choose a quiet spot and one book to enjoy together. Take turns talking about a favorite moment in the story.",
    "instagram": "A quiet corner. A book to share. Make a little room for reading together. #FamilyReading",
    "image_brief": "An inviting book and soft lamp on a teal table, objects only, no text or people.",
    "source_ids": ["family_support"],
}


@override_settings(SOCIAL_OPENAI_API_KEY="test-key", ENABLE_DEMO_ACCESS=False)
class ContentPlannerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_user(
            username="planner",
            email="planner@example.com",
            role=CustomUser.Role.SUPER_ADMIN,
        )
        cls.teacher = CustomUser.objects.create_user(
            username="teacher",
            email="teacher@example.com",
            role=CustomUser.Role.TEACHER,
        )

    def setUp(self):
        self.plan = get_plan()
        self.plan.updated_by = self.admin
        self.plan.mode = ContentPlan.Mode.REVIEW
        self.plan.save()
        self.client.force_login(self.admin)

    def values(self, mode):
        return {
            "mode": mode,
            "weekday": 1,
            "posting_hour": 10,
            "audience": "families",
            "priorities": "",
            "post_to_facebook": True,
            "post_to_instagram": True,
        }

    def week(self):
        _prepare_weeks(self.plan)
        return ContentWeek.objects.order_by("planned_at").first()

    def test_slots_stay_at_ten_eastern_across_dst_and_have_preview_time(self):
        slots = upcoming_slots(self.plan, datetime(2026, 10, 23, 12, tzinfo=EASTERN))
        self.assertEqual(len(slots), 4)
        self.assertEqual({target.hour for _, target in slots}, {10})
        self.assertEqual(
            {target.utcoffset().total_seconds() for _, target in slots},
            {-14400, -18000},
        )
        self.assertTrue(all(target.weekday() == 1 for _, target in slots))
        slots = upcoming_slots(self.plan, datetime(2026, 10, 26, 11, tzinfo=EASTERN))
        self.assertEqual(slots[0][1].day, 3)

    def test_topup_does_not_add_fifth_week_before_imminent_post(self):
        first_day = datetime(2026, 10, 23, 12, tzinfo=EASTERN)
        with patch("apps.social.planner.timezone.now", return_value=first_day):
            _prepare_weeks(self.plan)
        with patch(
            "apps.social.planner.timezone.now",
            return_value=datetime(2026, 10, 26, 11, tzinfo=EASTERN),
        ):
            _prepare_weeks(self.plan)
        self.assertEqual(ContentWeek.objects.count(), 4)
        with patch(
            "apps.social.planner.timezone.now",
            return_value=datetime(2026, 10, 27, 11, tzinfo=EASTERN),
        ):
            _prepare_weeks(self.plan)
        self.assertEqual(ContentWeek.objects.count(), 5)

    def test_views_require_superadmin_and_actions_require_post(self):
        self.assertEqual(self.client.get(reverse("social:planner")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("social:plan_action")).status_code, 405
        )
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse("social:planner")).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("social:plan_action"), {"action": "preview"}
            ).status_code,
            403,
        )

    def test_form_rejects_invalid_hour_and_no_networks(self):
        data = self.values("review") | {"posting_hour": 2}
        response = self.client.post(reverse("social:planner"), data)
        self.assertContains(response, "Select a valid choice")
        data = self.values("review") | {
            "post_to_facebook": False,
            "post_to_instagram": False,
        }
        response = self.client.post(reverse("social:planner"), data)
        self.assertContains(response, "Choose Facebook, Instagram, or both")

    def test_automatic_requires_connected_accounts(self):
        with self.assertRaises(SocialError):
            save_plan(values=self.values("automatic"), actor=self.admin)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.mode, "review")

    @patch("apps.social.planner.generate_image", return_value=(b"image", "image/jpeg"))
    @patch(
        "apps.social.planner.review_content",
        return_value=(True, "Useful and supported."),
    )
    @patch("apps.social.planner.generate_content", return_value=CONTENT)
    def test_worker_fills_only_four_slots_reuses_image_and_review_stays_draft(
        self, writer, reviewer, image
    ):
        for _ in range(6):
            _run_locked()
        self.assertEqual(ContentWeek.objects.count(), 4)
        self.assertEqual(SocialPost.objects.count(), 4)
        self.assertEqual(image.call_count, 4)
        self.assertEqual(writer.call_count, 4)
        self.assertEqual(reviewer.call_count, 4)
        self.assertFalse(SocialPost.objects.exclude(status="draft").exists())
        post = SocialPost.objects.first()
        self.assertTrue(post.post_to_facebook and post.post_to_instagram)
        self.assertNotEqual(post.facebook_caption, post.instagram_caption)
        self.assertEqual(bytes(post.image_data), b"image")
        response = self.client.get(reverse("social:planner"))
        self.assertContains(response, CONTENT["title"], count=8)

    @patch("apps.social.planner.generate_image")
    @patch(
        "apps.social.planner.review_content", return_value=(False, "Unsupported claim.")
    )
    @patch("apps.social.planner.generate_content", return_value=CONTENT)
    def test_failed_editorial_review_holds_without_image_cost(
        self, writer, reviewer, image
    ):
        _run_locked()
        week = ContentWeek.objects.get(post__isnull=False)
        self.assertEqual(week.status, "held")
        self.assertEqual(week.post.status, "draft")
        image.assert_not_called()

    @patch("apps.social.planner.connected_account", return_value=object())
    @patch("apps.social.services._require_publishable")
    @patch("apps.social.planner.generate_image", return_value=(b"image", "image/jpeg"))
    @patch("apps.social.planner.review_content", return_value=(True, "Supported."))
    @patch("apps.social.planner.generate_content", return_value=CONTENT)
    def test_automatic_schedules_then_pause_cancels_only_automatic_posts(self, *mocks):
        save_plan(values=self.values("automatic"), actor=self.admin)
        _run_locked()
        week = ContentWeek.objects.get(post__isnull=False)
        self.assertEqual(week.post.status, "scheduled")
        self.assertTrue(week.auto_scheduled)
        manual = SocialPost.objects.create(
            status="scheduled", scheduled_at=timezone.now() + timedelta(days=1)
        )
        save_plan(values=self.values("paused"), actor=self.admin)
        week.post.refresh_from_db()
        manual.refresh_from_db()
        self.assertEqual(week.post.status, "draft")
        self.assertIsNone(week.post.scheduled_at)
        self.assertEqual(manual.status, "scheduled")
        self.assertEqual(_run_locked(), 0)

    @patch("apps.social.planner.generate_content")
    def test_skip_between_selection_and_claim_does_not_generate(self, writer):
        week = self.week()
        skip_week(week.pk, actor=self.admin)
        _generate_week(self.plan, week)
        writer.assert_not_called()

    @patch("apps.social.planner.generate_image", return_value=(b"image", "image/jpeg"))
    @patch("apps.social.planner.review_content", return_value=(True, "Supported."))
    def test_skip_during_provider_call_does_not_create_post(self, reviewer, image):
        week = self.week()

        def writing(_):
            skip_week(week.pk, actor=self.admin)
            return CONTENT

        with patch("apps.social.planner.generate_content", side_effect=writing):
            _generate_week(self.plan, week)
        week.refresh_from_db()
        self.assertEqual(week.status, "skipped")
        self.assertIsNone(week.post_id)
        _prepare_weeks(self.plan)
        self.assertEqual(ContentWeek.objects.count(), 4)

    @patch(
        "apps.social.planner.generate_image",
        side_effect=SocialError("Image provider unavailable."),
    )
    @patch("apps.social.planner.review_content", return_value=(True, "Supported."))
    @patch("apps.social.planner.generate_content", return_value=CONTENT)
    def test_retry_reuses_text_and_stops_at_three_attempts(
        self, writer, reviewer, image
    ):
        week = self.week()
        ContentWeek.objects.exclude(pk=week.pk).update(status="skipped")
        for _ in range(4):
            ContentWeek.objects.filter(pk=week.pk).update(next_attempt_at=None)
            _run_locked()
        week.refresh_from_db()
        self.assertEqual(week.status, "error")
        self.assertEqual(week.attempts, 3)
        self.assertEqual(image.call_count, 3)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(reviewer.call_count, 1)
        self.assertFalse(SocialPost.objects.exists())

    @patch("apps.social.planner.generate_image")
    @patch("apps.social.planner.review_content", return_value=(True, "Supported."))
    @patch("apps.social.planner.generate_content", return_value=CONTENT)
    def test_pause_while_image_generates_leaves_only_a_draft(
        self, writer, reviewer, image
    ):
        def pause(**kwargs):
            save_plan(values=self.values("paused"), actor=self.admin)
            return b"image", "image/jpeg"

        image.side_effect = pause
        _run_locked()
        self.assertEqual(SocialPost.objects.get().status, "draft")
        self.assertEqual(ContentPlan.objects.get().mode, "paused")

    @patch(
        "apps.social.management.commands.publish_social_posts.maintain_content_plan",
        return_value=1,
    )
    @patch(
        "apps.social.management.commands.publish_social_posts.publish_due",
        return_value=0,
    )
    def test_existing_cron_runs_publishing_and_planning(self, publisher, planner):
        from io import StringIO

        from django.core.management import call_command

        output = StringIO()
        call_command("publish_social_posts", stdout=output)
        publisher.assert_called_once()
        planner.assert_called_once()
        self.assertIn("Prepared 1 weekly", output.getvalue())

    def test_manual_cancel_marks_week_skipped(self):
        week = self.week()
        post = SocialPost.objects.create(
            status="scheduled", scheduled_at=week.planned_at
        )
        week.post, week.auto_scheduled = post, True
        week.save()
        cancel_schedule(post, actor=self.admin)
        week.refresh_from_db()
        self.assertEqual(week.status, "skipped")
        self.assertFalse(week.auto_scheduled)

    @patch("apps.social.services.publish_post")
    def test_publisher_rechecks_mode_before_claim(self, publish):
        week = self.week()
        post = SocialPost.objects.create(
            status="scheduled", scheduled_at=timezone.now() - timedelta(minutes=1)
        )
        week.post, week.auto_scheduled = post, True
        week.save()
        self.assertEqual(publish_due(), 0)
        publish.assert_not_called()
        post.refresh_from_db()
        self.assertEqual(post.status, "draft")

    def test_context_contains_no_private_records(self):
        context = _context(self.plan, self.week())
        self.assertEqual(
            set(context),
            {
                "approved_sources",
                "week_of",
                "pillar",
                "editorial_direction",
                "audience",
                "priorities",
                "recent_captions",
                "networks",
            },
        )
        self.assertNotIn(self.admin.username, str(context))

    def test_paused_preview_is_explicit_and_does_not_change_mode(self):
        save_plan(values=self.values("paused"), actor=self.admin)
        request_preview(actor=self.admin)
        self.plan.refresh_from_db()
        self.assertTrue(self.plan.preview_requested)
        self.assertEqual(self.plan.mode, "paused")

    def test_saving_paused_stops_a_pending_preview_request(self):
        request_preview(actor=self.admin)
        save_plan(values=self.values("paused"), actor=self.admin)
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.preview_requested)
        self.assertEqual(_run_locked(), 0)

    def test_advisory_lock_released_after_failure(self):
        with (
            patch(
                "apps.social.planner._run_locked",
                side_effect=RuntimeError("unexpected"),
            ),
            self.assertRaises(RuntimeError),
        ):
            maintain_content_plan()
        with patch("apps.social.planner._run_locked", return_value=7):
            self.assertEqual(maintain_content_plan(), 7)

    def test_response_validation_blocks_links_missing_sources_repetition(self):
        validate_content(CONTENT, [])
        for candidate in [
            CONTENT | {"source_ids": []},
            CONTENT | {"facebook": "Visit https://evil.example for a quick answer."},
            CONTENT | {"title": ""},
        ]:
            with self.assertRaises(SocialError):
                validate_content(candidate, [])
        with self.assertRaises(SocialError):
            validate_content(CONTENT, [CONTENT["facebook"]])

    @patch("apps.social.planner_ai.structured")
    def test_brand_week_requires_brand_and_structured_literacy_in_both_captions(
        self, writer
    ):
        writer.return_value = CONTENT
        with self.assertRaises(SocialError):
            generate_content({"pillar": "clearcode_approach"})
        writer.return_value = CONTENT | {
            "source_ids": ["clearcode"],
            "facebook": "ClearCode Reading uses explicit, systematic instruction to teach reading skills. Learn about its structured literacy approach.",
            "instagram": "ClearCode Reading: structured literacy, explained step by step. Learn about the approach.",
        }
        self.assertEqual(
            generate_content({"pillar": "clearcode_approach"})["source_ids"],
            ["clearcode"],
        )

    @patch("apps.social.planner_ai._post")
    def test_incomplete_refused_and_malformed_responses_are_rejected(self, post):
        for body in [
            {"status": "incomplete"},
            {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "refusal"}]}],
            },
            {"status": "completed", "output": None},
        ]:
            post.return_value = body
            with self.assertRaises(SocialError):
                structured("test", {}, {}, "test")
        payload = post.call_args.args[1]
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
