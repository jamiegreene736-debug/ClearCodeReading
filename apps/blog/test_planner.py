from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import BlogContentPlan, BlogContentWeek, BlogPost
from apps.blog.planner import (
    _generate_week,
    _prepare_weeks,
    _run_locked,
    get_plan,
    request_preview,
    save_plan,
    schedule_week,
    skip_week,
)
from apps.blog.planner_ai import render_body, validate_article
from apps.blog.promotion import feature_article, validate_promotion
from apps.social.crypto import encrypt_text
from apps.social.exceptions import SocialError
from apps.social.meta import publish_facebook
from apps.social.models import SocialAccount, SocialPost
from apps.social.services import publish_due, schedule_post
from apps.social.tests import META
from apps.users.models import CustomUser

PARAGRAPH = " ".join(
    [
        "Read together for a few quiet minutes. Let your child choose a familiar book and talk about the story after you finish a page."
    ]
    * 6
)
CONTENT = {
    "title": "Make a little time for shared reading",
    "excerpt": "A practical way to make shared reading a regular part of family life.",
    "seo_title": "A simple shared reading routine",
    "seo_description": "Make time for a book together with a small, flexible family routine.",
    "why": "Readers get a concrete way to start a manageable family reading routine.",
    "cover_alt": "An open book beside a teal lamp on a table",
    "image_brief": "An objects-only illustration of an open book beside a teal lamp. No people or text.",
    "facebook": "Make a little room for shared reading. Our new article offers a simple routine to try together.",
    "source_ids": ["family_support"],
    "sections": [
        {"heading": heading, "paragraphs": [PARAGRAPH]}
        for heading in [
            "Choose a quiet moment",
            "Share a familiar book",
            "Keep it manageable",
        ]
    ],
}


@override_settings(**META, SOCIAL_OPENAI_API_KEY="test-key")
class BlogPlannerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_user(
            username="blog-planner",
            email="blogplanner@example.com",
            role=CustomUser.Role.SUPER_ADMIN,
        )
        cls.teacher = CustomUser.objects.create_user(
            username="blog-teacher",
            email="blogteacher@example.com",
            role=CustomUser.Role.TEACHER,
        )
        SocialAccount.objects.create(
            network="facebook",
            status="connected",
            external_id="page-1",
            encrypted_token=encrypt_text("test-token"),
            connected_by=cls.admin,
        )

    def setUp(self):
        self.plan = get_plan()
        self.plan.updated_by = self.admin
        self.plan.mode = BlogContentPlan.Mode.REVIEW
        self.plan.save()
        self.client.force_login(self.admin)

    def values(self, mode):
        return {
            "mode": mode,
            "weekday": 2,
            "posting_hour": 9,
            "audience": "families",
            "priorities": "",
            "promote_facebook": True,
            "promotion_delay_hours": 1,
        }

    def ready_week(self):
        _prepare_weeks(self.plan)
        week = BlogContentWeek.objects.first()
        with (
            patch("apps.blog.planner.generate_article", return_value=deepcopy(CONTENT)),
            patch(
                "apps.blog.planner.review_article",
                return_value=(True, "Approved useful reading routine."),
            ),
            patch(
                "apps.blog.planner.generate_image",
                return_value=(b"image", "image/jpeg"),
            ),
        ):
            _generate_week(self.plan, week)
        week.refresh_from_db()
        return week

    def article(self, **kwargs):
        return BlogPost.objects.create(
            title="A reading article",
            excerpt="A useful reading idea for families.",
            body="A helpful article.",
            status="published",
            published_at=timezone.now() + timedelta(days=2),
            **kwargs,
        )

    def test_prepare_four_drafts_with_cover_and_facebook_teaser_idempotently(self):
        week = self.ready_week()
        self.assertEqual(BlogContentWeek.objects.count(), 4)
        self.assertTrue(week.post.has_cover)
        self.assertEqual(week.post.display_author, "ClearCode Reading")
        self.assertEqual(week.post.status, "draft")
        promotion = week.post.facebook_promotion
        self.assertEqual(promotion.source, "blog")
        self.assertFalse(promotion.post_to_instagram)
        self.assertEqual(promotion.status, "draft")
        _generate_week(self.plan, week)
        self.assertEqual(BlogPost.objects.count(), 1)
        self.assertEqual(SocialPost.objects.count(), 1)
        response = self.client.get(reverse("blog_manage:planner"))
        self.assertContains(response, "Read full article")
        self.assertContains(response, CONTENT["facebook"])
        self.assertContains(
            self.client.get(reverse("blog_manage:list")), "AI blog plan"
        )
        self.assertContains(
            self.client.get(reverse("blog_manage:edit", args=[week.post_id])),
            "Feature on Facebook",
        )

    def test_schedule_pair_in_actual_marketing_queue(self):
        week = self.ready_week()
        schedule_week(week.pk, actor=self.admin)
        week.refresh_from_db()
        self.assertTrue(week.post.is_scheduled)
        promotion = week.post.facebook_promotion
        self.assertEqual(promotion.status, "scheduled")
        self.assertEqual(promotion.scheduled_at, week.planned_at + timedelta(hours=1))
        self.assertEqual(BlogPost.objects.published().count(), 0)
        response = self.client.get(reverse("social:queue"))
        self.assertContains(response, "Blog feature")
        self.assertContains(response, week.post.title)
        with self.assertRaises(SocialError):
            schedule_week(week.pk, actor=self.admin)

    def test_automatic_schedules_approved_pair_and_pause_holds_it(self):
        week = self.ready_week()
        save_plan(values=self.values("automatic"), actor=self.admin)
        schedule_week(week.pk, actor=self.admin, automatic=True)
        save_plan(values=self.values("paused"), actor=self.admin)
        week.refresh_from_db()
        self.assertEqual(week.post.status, "draft")
        self.assertEqual(week.post.facebook_promotion.status, "draft")
        self.assertEqual(week.status, "held")
        save_plan(values=self.values("automatic"), actor=self.admin)
        with self.assertRaises(SocialError):
            schedule_week(week.pk, actor=self.admin, automatic=True)

    def test_manual_pair_survives_pause(self):
        week = self.ready_week()
        schedule_week(week.pk, actor=self.admin)
        save_plan(values=self.values("paused"), actor=self.admin)
        week.refresh_from_db()
        self.assertTrue(week.post.is_scheduled)
        self.assertEqual(week.post.facebook_promotion.status, "scheduled")

    def test_reschedule_and_slug_change_follow_article(self):
        article = self.article()
        when = article.published_at + timedelta(hours=2)
        promotion = feature_article(
            article, caption=CONTENT["facebook"], when=when, actor=self.admin
        )
        article.published_at += timedelta(days=3)
        article.slug = "changed-reading-slug"
        article.save()
        promotion.refresh_from_db()
        self.assertEqual(promotion.scheduled_at, when + timedelta(days=3))
        self.assertTrue(promotion.link_url.endswith("/changed-reading-slug/"))
        self.assertEqual(SocialPost.objects.count(), 1)

    def test_unpublish_and_delete_hold_share(self):
        article = self.article()
        promotion = feature_article(
            article,
            caption=CONTENT["facebook"],
            when=article.published_at + timedelta(hours=1),
            actor=self.admin,
        )
        article.status = "draft"
        article.save()
        promotion.refresh_from_db()
        self.assertEqual(promotion.status, "attention")
        article.status = "published"
        article.save()
        promotion.refresh_from_db()
        self.assertEqual(promotion.status, "attention")
        article.delete()
        promotion.refresh_from_db()
        self.assertIsNone(promotion.blog_post_id)
        self.assertTrue(promotion.is_blog_promotion)
        with self.assertRaises(SocialError):
            validate_promotion(promotion, sending=True)

    def test_reject_feature_before_publication_and_on_drafts(self):
        article = self.article()
        with self.assertRaises(SocialError):
            feature_article(
                article,
                caption=CONTENT["facebook"],
                when=article.published_at,
                actor=self.admin,
            )
        article.status = "draft"
        article.save()
        with self.assertRaises(SocialError):
            feature_article(
                article,
                caption=CONTENT["facebook"],
                when=article.published_at + timedelta(hours=1),
                actor=self.admin,
            )
        self.assertFalse(SocialPost.objects.exists())

    def test_generic_social_schedule_also_enforces_article_time(self):
        article = self.article()
        promotion = SocialPost.objects.create(
            blog_post=article,
            source="blog",
            facebook_caption=CONTENT["facebook"],
            post_to_instagram=False,
        )
        with self.assertRaises(SocialError):
            schedule_post(promotion, article.published_at, actor=self.admin)

    def test_final_delivery_gate_holds_unavailable_public_article(self):
        article = self.article()
        article.published_at = timezone.now() - timedelta(hours=2)
        article.save()
        post = SocialPost.objects.create(
            blog_post=article,
            source="blog",
            facebook_caption=CONTENT["facebook"],
            post_to_instagram=False,
            status="scheduled",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        with (
            patch(
                "apps.blog.promotion.requests.get",
                return_value=Mock(status_code=404, text="Not found"),
            ),
            patch("apps.social.meta.publish_facebook") as send,
        ):
            self.assertEqual(publish_due(), 1)
            send.assert_not_called()
        post.refresh_from_db()
        self.assertEqual(post.status, "attention")
        self.assertIn("broken link", post.last_error)

    def test_final_gate_handles_timeout_and_future_bulk_update(self):
        article = self.article()
        article.published_at = timezone.now() - timedelta(hours=2)
        article.save()
        promotion = SocialPost.objects.create(
            blog_post=article,
            source="blog",
            facebook_caption=CONTENT["facebook"],
            post_to_instagram=False,
        )
        with (
            patch("apps.blog.promotion.requests.get", side_effect=requests.Timeout),
            self.assertRaises(SocialError),
        ):
            validate_promotion(promotion, sending=True)
        BlogPost.objects.filter(pk=article.pk).update(
            published_at=timezone.now() + timedelta(days=1)
        )
        with (
            patch("apps.blog.promotion.requests.get") as fetch,
            self.assertRaises(SocialError),
        ):
            validate_promotion(promotion, sending=True)
        fetch.assert_not_called()

    def test_facebook_uses_link_feed_even_if_an_image_is_attached(self):
        article = self.article()
        post = SocialPost(
            blog_post=article,
            source="blog",
            facebook_caption=CONTENT["facebook"],
            link_url="https://example.com/blog/article/",
            image_data=b"image",
            post_to_instagram=False,
        )
        with patch(
            "apps.social.meta._graph",
            side_effect=[
                {"id": "fb-post"},
                {"permalink_url": "https://facebook.com/post"},
            ],
        ) as graph:
            publish_facebook(
                post,
                SocialAccount.objects.get(network="facebook"),
                image_url="https://example.com/image.jpg",
            )
        self.assertTrue(graph.call_args_list[0].args[1].endswith("/feed"))
        self.assertEqual(graph.call_args_list[0].kwargs["data"]["link"], post.link_url)

    def test_edited_article_does_not_bypass_editorial_review(self):
        week = self.ready_week()
        week.post.body = "Changed content after the check."
        week.post.save()
        with self.assertRaises(SocialError):
            schedule_week(week.pk, actor=self.admin)

    def test_reviewer_hold_does_not_generate_image_or_schedule(self):
        _prepare_weeks(self.plan)
        week = BlogContentWeek.objects.first()
        with (
            patch("apps.blog.planner.generate_article", return_value=CONTENT),
            patch(
                "apps.blog.planner.review_article",
                return_value=(False, "Needs human review."),
            ),
            patch("apps.blog.planner.generate_image") as image,
        ):
            _generate_week(self.plan, week)
        image.assert_not_called()
        week.refresh_from_db()
        self.assertEqual(week.status, "held")
        self.assertEqual(week.post.status, "draft")

    def test_generation_failure_retries_with_backoff_without_duplicate_article(self):
        with patch(
            "apps.blog.planner.generate_article",
            side_effect=SocialError("Rate limited"),
        ):
            self.assertEqual(_run_locked(), 0)
        week = BlogContentWeek.objects.first()
        self.assertEqual(week.attempts, 1)
        self.assertGreater(week.next_attempt_at, timezone.now())
        self.assertEqual(BlogPost.objects.count(), 0)

    def test_paused_requires_explicit_preview_request(self):
        self.plan.mode = "paused"
        self.plan.save()
        self.assertEqual(_run_locked(), 0)
        self.assertFalse(BlogContentWeek.objects.exists())
        request_preview(actor=self.admin)
        self.plan.refresh_from_db()
        self.assertTrue(self.plan.preview_requested)

    def test_skipping_during_generation_does_not_create_article(self):
        _prepare_weeks(self.plan)
        week = BlogContentWeek.objects.first()

        def generate(_):
            skip_week(week.pk, actor=self.admin)
            return CONTENT

        with (
            patch("apps.blog.planner.generate_article", side_effect=generate),
            patch("apps.blog.planner.generate_image") as image,
        ):
            _generate_week(self.plan, week)
        image.assert_not_called()
        self.assertFalse(BlogPost.objects.exists())

    def test_authorization_and_post_only_actions(self):
        self.assertEqual(
            self.client.get(reverse("blog_manage:plan_action")).status_code, 405
        )
        self.client.force_login(self.teacher)
        self.assertEqual(
            self.client.get(reverse("blog_manage:planner")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("blog_manage:plan_action"), {"action": "preview"}
            ).status_code,
            403,
        )
        article = self.article()
        self.assertEqual(
            self.client.get(
                reverse("blog_manage:facebook_feature", args=[article.pk])
            ).status_code,
            403,
        )

    def test_schema_validation_and_html_escaping(self):
        validate_article(CONTENT, [])
        with self.assertRaises(SocialError):
            validate_article(CONTENT, [CONTENT["title"]])
        bad = deepcopy(CONTENT)
        bad["sections"] = []
        with self.assertRaises(SocialError):
            validate_article(bad, [])
        bad = deepcopy(CONTENT)
        bad["source_ids"] = ["invented"]
        with self.assertRaises(SocialError):
            validate_article(bad, [])
        bad = deepcopy(CONTENT)
        bad["sections"][0]["heading"] = '<script>alert("x")</script>'
        self.assertNotIn("<script>", render_body(bad))
        self.assertIn("&lt;script&gt;", render_body(bad))

    def test_public_article_confirmed_before_single_successful_send(self):
        article = self.article()
        article.published_at = timezone.now() - timedelta(hours=2)
        article.save()
        post = SocialPost.objects.create(
            blog_post=article,
            source="blog",
            facebook_caption=CONTENT["facebook"],
            post_to_instagram=False,
            status="scheduled",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertContains(
            self.client.get(article.get_absolute_url()),
            f'data-blog-post-id="{article.pk}"',
        )
        with (
            patch(
                "apps.blog.promotion.requests.get",
                return_value=Mock(
                    status_code=200, text=f'<article data-blog-post-id="{article.pk}">'
                ),
            ),
            patch(
                "apps.social.meta.publish_facebook",
                return_value={
                    "external_id": "post-1",
                    "permalink": "https://facebook.com/post-1",
                },
            ) as send,
        ):
            self.assertEqual(publish_due(), 1)
            self.assertEqual(publish_due(), 0)
            send.assert_called_once()
        post.refresh_from_db()
        self.assertEqual(post.status, "posted")
        with self.assertRaises(SocialError):
            feature_article(
                article,
                caption=CONTENT["facebook"],
                when=timezone.now() + timedelta(days=1),
                actor=self.admin,
            )

    def test_manual_retry_failure_remains_actionable(self):
        article = self.article()
        post = SocialPost.objects.create(
            blog_post=article,
            source="blog",
            facebook_caption=CONTENT["facebook"],
            post_to_instagram=False,
            status="attention",
        )
        self.client.post(reverse("social:retry", args=[post.pk]))
        post.refresh_from_db()
        self.assertEqual(post.status, "attention")
        self.assertIn("not public yet", post.last_error)

    def test_feature_form_uses_eastern_and_updates_existing_row(self):
        from apps.social.services import EASTERN

        article = self.article()
        when = (
            (article.published_at + timedelta(hours=2))
            .astimezone(EASTERN)
            .replace(second=0, microsecond=0)
        )
        url = reverse("blog_manage:facebook_feature", args=[article.pk])
        response = self.client.post(
            url,
            {
                "caption": CONTENT["facebook"],
                "scheduled_at": when.strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.assertRedirects(response, reverse("social:queue"))
        self.assertEqual(SocialPost.objects.get().scheduled_at, when)
        self.client.post(
            url,
            {
                "caption": CONTENT["facebook"],
                "scheduled_at": (when + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.assertEqual(SocialPost.objects.count(), 1)

    def test_worker_runs_blog_even_if_social_planner_fails(self):
        from django.core.management import call_command

        with (
            patch(
                "apps.social.management.commands.publish_social_posts.publish_due",
                return_value=0,
            ),
            patch(
                "apps.social.management.commands.publish_social_posts.maintain_content_plan",
                side_effect=RuntimeError("dependency unavailable"),
            ),
            patch("apps.blog.planner.maintain_blog_plan", return_value=0) as blog,
            self.assertRaises(RuntimeError),
        ):
            call_command("publish_social_posts")
        blog.assert_called_once()
