from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import BlogPost
from apps.social.exceptions import SocialError
from apps.social.models import ContentPlan, ContentWeek, SocialPost, SocialPublication
from apps.social.planner import _prepare_weeks, _schedule_ready
from apps.social.schedule_views import (
    calendar_days,
    month_start,
)
from apps.social.services import EASTERN, delete_unpublished_post, publish_due
from apps.social.tests import META
from apps.users.models import AuditLog, CustomUser


@override_settings(**META)
class ScheduleViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_user(
            username="schedule-admin",
            email="schedule@example.com",
            role=CustomUser.Role.SUPER_ADMIN,
        )
        cls.teacher = CustomUser.objects.create_user(
            username="calendar-teacher",
            email="teacher-calendar@example.com",
            role=CustomUser.Role.TEACHER,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def post(self, **values):
        return SocialPost.objects.create(
            facebook_caption="A practical reading moment to share together.",
            created_by=self.admin,
            **values,
        )

    def test_confirm_then_delete_draft_and_audit(self):
        post = self.post(image_data=b"image")
        url = reverse("social:delete", args=[post.pk])
        self.assertContains(self.client.get(url), "Delete post permanently")
        self.assertTrue(SocialPost.objects.filter(pk=post.pk).exists())
        response = self.client.post(url)
        self.assertRedirects(response, reverse("social:queue") + "?tab=drafts")
        self.assertFalse(SocialPost.objects.filter(pk=post.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action="marketing.social.deleted", entity_id=str(post.pk)
            ).exists()
        )

    def test_scheduled_delete_removes_from_queue_and_publisher(self):
        post = self.post(
            status="scheduled", scheduled_at=timezone.now() - timedelta(minutes=1)
        )
        delete_unpublished_post(post.pk, actor=self.admin)
        with patch("apps.social.services.publish_post") as send:
            self.assertEqual(publish_due(), 0)
            send.assert_not_called()

    def test_deletion_retires_ai_slot_without_refilling_it(self):
        plan = ContentPlan.objects.create(updated_by=self.admin, mode="automatic")
        _prepare_weeks(plan)
        week = ContentWeek.objects.first()
        post = self.post(status="scheduled", scheduled_at=week.planned_at)
        week.post, week.auto_scheduled, week.review_passed, week.status = (
            post,
            True,
            True,
            "ready",
        )
        week.save()
        delete_unpublished_post(post.pk, actor=self.admin)
        _prepare_weeks(plan)
        _schedule_ready(plan)
        week.refresh_from_db()
        self.assertEqual(week.status, "skipped")
        self.assertIsNone(week.post_id)
        self.assertFalse(week.auto_scheduled)
        self.assertEqual(ContentWeek.objects.count(), 4)
        self.assertFalse(SocialPost.objects.exists())

    def test_deleting_facebook_feature_preserves_article(self):
        article = BlogPost.objects.create(
            title="Keep this article",
            excerpt="A useful article.",
            body="Article content.",
            status="published",
            published_at=timezone.now() + timedelta(days=3),
        )
        promotion = self.post(
            source="blog",
            blog_post=article,
            status="scheduled",
            scheduled_at=article.published_at + timedelta(hours=1),
        )
        delete_unpublished_post(promotion.pk, actor=self.admin)
        article.refresh_from_db()
        self.assertEqual(article.status, "published")
        self.assertTrue(article.is_scheduled)
        self.assertFalse(SocialPost.objects.exists())

    def test_sending_post_cannot_be_deleted_after_confirmation_page_loaded(self):
        post = self.post(status="scheduled", scheduled_at=timezone.now())
        url = reverse("social:delete", args=[post.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        SocialPost.objects.filter(pk=post.pk).update(status="publishing")
        self.client.post(url)
        self.assertEqual(SocialPost.objects.get(pk=post.pk).status, "publishing")

    def test_published_and_partially_published_posts_are_protected(self):
        for status in ["publishing", "posted", "attention"]:
            with self.subTest(status=status):
                post = self.post(status=status)
                with self.assertRaises(SocialError):
                    delete_unpublished_post(post.pk, actor=self.admin)
        post = self.post()
        SocialPublication.objects.create(
            post=post, network="facebook", status="published", external_id="remote-id"
        )
        with self.assertRaises(SocialError):
            delete_unpublished_post(post.pk, actor=self.admin)

    def test_permissions_and_csrf(self):
        post = self.post()
        url = reverse("social:delete", args=[post.pk])
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.client.get(reverse("social:calendar")).status_code, 403)
        with self.assertRaises(SocialError):
            delete_unpublished_post(post.pk, actor=self.teacher)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(url).status_code, 403)
        self.assertTrue(SocialPost.objects.filter(pk=post.pk).exists())

    def test_deleted_post_in_publish_snapshot_is_skipped(self):
        post = self.post(
            status="scheduled", scheduled_at=timezone.now() - timedelta(minutes=1)
        )
        original = ContentPlan.objects.select_for_update

        def concurrent_delete(*args, **kwargs):
            SocialPost.objects.filter(pk=post.pk).delete()
            return original(*args, **kwargs)

        with (
            patch.object(
                ContentPlan.objects, "select_for_update", side_effect=concurrent_delete
            ),
            patch("apps.social.services.publish_post") as send,
        ):
            self.assertEqual(publish_due(), 0)
            send.assert_not_called()

    def test_ai_editor_does_not_recreate_a_deleted_post(self):
        post = self.post()

        def generate(**kwargs):
            delete_unpublished_post(post.pk, actor=self.admin)
            return "A new generated Facebook caption.", "A new Instagram caption."

        with patch("apps.social.views.write_captions", side_effect=generate):
            response = self.client.post(
                reverse("social:edit", args=[post.pk]),
                {
                    "mode": "brief",
                    "action": "draft_ai",
                    "brief": "A quiet reading routine",
                    "post_to_facebook": "on",
                },
            )
        self.assertContains(response, "has not been recreated")
        self.assertFalse(SocialPost.objects.filter(pk=post.pk).exists())

    def test_calendar_buckets_by_eastern_day_and_orders_multiple_posts(self):
        earlier = self.post(
            status="scheduled",
            scheduled_at=datetime(2026, 11, 1, 0, 15, tzinfo=dt_timezone.utc),
        )
        later = self.post(
            status="scheduled",
            scheduled_at=datetime(2026, 11, 1, 1, 15, tzinfo=dt_timezone.utc),
        )
        self.post(status="draft", scheduled_at=earlier.scheduled_at)
        weeks = calendar_days(date(2026, 10, 1), date(2026, 10, 2))
        day = next(
            day for week in weeks for day in week if day["date"] == date(2026, 10, 31)
        )
        self.assertEqual([p.pk for p in day["posts"]], [earlier.pk, later.pk])
        response = self.client.get(reverse("social:calendar"), {"month": "2026-10"})
        self.assertContains(response, "8:15 PM")
        self.assertContains(response, "9:15 PM")
        self.assertContains(response, reverse("social:delete", args=[earlier.pk]))

    def test_calendar_month_boundary_handles_dst(self):
        before = self.post(
            status="scheduled",
            scheduled_at=datetime(2026, 11, 1, 3, 59, tzinfo=dt_timezone.utc),
        )
        first = self.post(
            status="scheduled",
            scheduled_at=datetime(2026, 11, 1, 4, 0, tzinfo=dt_timezone.utc),
        )
        after_dst = self.post(
            status="scheduled",
            scheduled_at=datetime(2026, 11, 2, 5, 0, tzinfo=dt_timezone.utc),
        )
        days = {
            day["date"]: day
            for week in calendar_days(date(2026, 11, 1), date(2026, 11, 2))
            for day in week
        }
        self.assertEqual([p.pk for p in days[date(2026, 11, 1)]["posts"]], [first.pk])
        self.assertEqual(
            [p.pk for p in days[date(2026, 11, 2)]["posts"]], [after_dst.pk]
        )
        self.assertNotIn(
            before.pk, [p.pk for day in days.values() for p in day["posts"]]
        )

    def test_calendar_shows_blog_features_and_month_navigation(self):
        article = BlogPost.objects.create(
            title="A linked blog article", excerpt="Reading support.", body="Article."
        )
        post = self.post(
            source="blog",
            blog_post=article,
            status="scheduled",
            scheduled_at=datetime(2026, 12, 2, 9, tzinfo=EASTERN),
        )
        response = self.client.get(reverse("social:calendar"), {"month": "2026-12"})
        self.assertContains(response, article.title)
        self.assertContains(response, "Blog feature")
        self.assertContains(response, "?month=2027-01")
        self.assertContains(response, f'data-post-id="{post.pk}"')
        for value in ["junk", "2026-13", "0001-01", "9999-12", "2026-10-12"]:
            self.assertEqual(month_start(value, date(2026, 10, 2)), date(2026, 10, 1))
        self.assertEqual(len(calendar_days(date(2028, 2, 1), date(2028, 2, 29))), 5)

    def test_delete_controls_and_calendar_links_are_visible(self):
        post = self.post()
        self.assertContains(
            self.client.get(reverse("social:queue") + "?tab=drafts"),
            reverse("social:delete", args=[post.pk]),
        )
        self.assertContains(
            self.client.get(reverse("social:edit", args=[post.pk])), "Delete post"
        )
        self.assertContains(self.client.get(reverse("social:planner")), "Calendar view")

    def test_schedule_picker_browses_months_without_changing_selection(self):
        post = self.post()
        response = self.client.get(
            reverse("social:schedule", args=[post.pk]),
            {
                "date": "2026-12-31",
                "month": "2027-01",
                "time": "14:35",
            },
        )
        self.assertContains(response, "January 2027")
        self.assertContains(response, "Previous month")
        self.assertContains(response, "Next month")
        self.assertEqual(response.context["previous"], "2026-12")
        self.assertEqual(response.context["following"], "2027-02")
        self.assertEqual(response.context["selected_date"], "2026-12-31")
        self.assertEqual(response.context["selected_time"], "14:35")
        self.assertFalse(
            any(
                day["is_selected"]
                for week in response.context["weeks"]
                for day in week
                if day["in_month"]
            )
        )

    def test_schedule_picker_leap_day_and_invalid_dates(self):
        post = self.post()
        url = reverse("social:schedule", args=[post.pk])
        response = self.client.get(url, {"date": "2028-02-29"})
        selected = [
            day["day"]
            for week in response.context["weeks"]
            for day in week
            if day["is_selected"]
        ]
        self.assertEqual(selected, [date(2028, 2, 29)])
        for value in ["junk", "0001-01-01", "9999-12-31"]:
            self.assertEqual(
                self.client.get(url, {"date": value, "month": value}).status_code, 200
            )

    def test_composer_rejects_missing_content_before_save_or_publish(self):
        cases = [
            ({"post_to_facebook": "on", "caption": "  "}, "Facebook caption"),
            (
                {"post_to_instagram": "on", "caption": "Ready caption"},
                "Instagram needs a photo",
            ),
            ({"caption": "Ready caption"}, "Choose Facebook"),
        ]
        for action in ["schedule", "post_now"]:
            for fields, error in cases:
                with (
                    self.subTest(action=action, fields=fields),
                    patch("apps.social.views.claim_and_publish_now") as publish,
                ):
                    response = self.client.post(
                        reverse("social:new"),
                        {"mode": "manual", "action": action, **fields},
                    )
                    self.assertContains(response, error)
                    self.assertFalse(SocialPost.objects.exists())
                    publish.assert_not_called()

    def test_composer_allows_facebook_text_only_and_incomplete_drafts(self):
        response = self.client.post(
            reverse("social:new"),
            {
                "mode": "manual",
                "action": "schedule",
                "caption": "Ready for Facebook.",
                "post_to_facebook": "on",
            },
        )
        post = SocialPost.objects.get()
        self.assertRedirects(response, reverse("social:schedule", args=[post.pk]))
        response = self.client.post(
            reverse("social:new"), {"mode": "brief", "action": "save"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SocialPost.objects.count(), 2)

    def test_invalid_edit_does_not_overwrite_existing_content(self):
        post = self.post(post_to_instagram=False)
        response = self.client.post(
            reverse("social:edit", args=[post.pk]),
            {
                "mode": "brief",
                "action": "schedule",
                "facebook_caption": " ",
                "post_to_facebook": "on",
            },
        )
        self.assertContains(response, "Facebook caption")
        post.refresh_from_db()
        self.assertEqual(
            post.facebook_caption, "A practical reading moment to share together."
        )
