from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.social.ai import image_prompt
from apps.social.carousel_ai import plan_carousel
from apps.social.crypto import encrypt_text
from apps.social.exceptions import SocialError
from apps.social.models import SocialAccount, SocialPost
from apps.social.reel_publishing import publish_reel
from apps.social.reels import (
    MAX_VIDEO_BYTES,
    VideoRejected,
    _download,
    _request,
    plan_reel,
    refresh_reel,
    start_reel,
)
from apps.social.services import (
    delete_unpublished_post,
    publish_post,
    require_post_content,
)
from apps.social.tests import META
from apps.users.models import CustomUser

MP4 = b"\x00\x00\x00\x18ftypisom" + b"video-data" * 10
STORY = "0–3s: A fictional child turns away from a visible tablet. 3–8s: They bring a book to a caregiver. 8–12s: Both settle in and read together."


@override_settings(**META, SOCIAL_OPENAI_API_KEY="test-key")
class ReelTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="reels-admin", is_superuser=True
        )
        self.client.force_login(self.user)
        self.post = SocialPost.objects.create(
            is_reel=True,
            reel_prompt=STORY,
            brief="A child wants a human to read, not a tablet.",
            facebook_caption="Try reading together for a few minutes.",
            instagram_caption="Read together tonight.",
            created_by=self.user,
        )

    def ready(self):
        self.post.video_data = MP4
        self.post.reel_job = {"id": "video_test", "status": "completed"}
        self.post.save()

    def account(self, network):
        return SocialAccount.objects.create(
            network=network,
            external_id="12345",
            status="connected",
            encrypted_token=encrypt_text("provider-test-token"),
        )

    def test_carousel_directions_preserve_tablet_and_emotion(self):
        slides = [
            "A child looks bored beside a visible tablet.",
            "A child brings a book to a caregiver.",
            "A family enjoys reading together on a couch.",
        ]
        with patch(
            "apps.social.carousel_ai._write_json",
            return_value={
                "facebook": "A warm reading routine to try tonight.",
                "instagram": "Read together tonight.",
                "slides": slides,
            },
        ) as write:
            self.assertEqual(
                plan_carousel(
                    subject=self.post.brief, count=3, audience="families", tone="warm"
                )["slides"],
                slides,
            )
        instructions = write.call_args.args[0]
        self.assertIn("visibly disengaged child", instructions)
        self.assertIn("Vary framing", instructions)
        prompt = image_prompt(slides[0])
        self.assertIn(slides[0], prompt)
        self.assertIn("do not replace that scene with a smiling lesson", prompt)

    def test_plan_is_editable_and_has_motion_directions(self):
        with patch(
            "apps.social.reels._write_json",
            return_value={
                "storyboard": STORY,
                "facebook": self.post.facebook_caption,
                "instagram": self.post.instagram_caption,
            },
        ) as write:
            plan = plan_reel(subject=self.post.brief, audience="families", tone="warm")
        self.assertEqual(plan["storyboard"], STORY)
        self.assertIn("visible motion", write.call_args.args[0])
        with (
            patch("apps.social.reels._write_json", return_value={"storyboard": []}),
            self.assertRaises(SocialError),
        ):
            plan_reel(subject=self.post.brief, audience="families", tone="warm")

    def test_start_saves_job_and_blocks_duplicate_paid_request(self):
        with patch("apps.social.reels._request") as call:
            call.return_value.json.return_value = {
                "id": "video_test",
                "status": "queued",
            }
            start_reel(self.post.pk)
            with self.assertRaises(SocialError):
                start_reel(self.post.pk)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs["data"]["size"], "720x1280")
        self.assertEqual(call.call_args.kwargs["data"]["seconds"], "12")
        self.post.refresh_from_db()
        self.assertEqual(self.post.reel_job["id"], "video_test")

    def test_timeout_holds_submission_and_keeps_existing_draft(self):
        with (
            patch("apps.social.reels._request", side_effect=SocialError("timeout")),
            self.assertRaisesMessage(SocialError, "second charge"),
        ):
            start_reel(self.post.pk)
        self.post.refresh_from_db()
        self.assertEqual(self.post.reel_job["status"], "uncertain")
        self.assertEqual(self.post.reel_prompt, STORY)
        with (
            patch("apps.social.reels._request") as call,
            self.assertRaises(SocialError),
        ):
            start_reel(self.post.pk)
        call.assert_not_called()

    def test_refresh_persists_video_and_throttles_polling(self):
        self.post.reel_job = {"id": "video_test", "status": "queued", "checked": 0}
        self.post.save()
        with (
            patch("apps.social.reels._request") as call,
            patch("apps.social.reels._download", return_value=MP4),
        ):
            call.return_value.json.return_value = {
                "status": "completed",
                "progress": 100,
            }
            self.assertEqual(refresh_reel(self.post.pk)["status"], "completed")
            refresh_reel(self.post.pk)
        self.assertEqual(call.call_count, 1)
        self.post.refresh_from_db()
        self.assertEqual(bytes(self.post.video_data), MP4)
        self.post.reel_job = {
            "id": "video_test",
            "status": "queued",
            "checked": timezone.now().timestamp(),
        }
        self.post.save()
        with patch("apps.social.reels._request") as call:
            refresh_reel(self.post.pk)
        call.assert_not_called()

    def test_download_failure_resumes_same_job(self):
        self.post.reel_job = {"id": "video_test", "status": "queued", "checked": 0}
        self.post.save()
        with (
            patch("apps.social.reels._request") as call,
            patch("apps.social.reels._download", side_effect=requests.Timeout),
        ):
            call.return_value.json.return_value = {"status": "completed"}
            with self.assertRaises(SocialError):
                refresh_reel(self.post.pk)
        self.post.refresh_from_db()
        self.assertEqual(self.post.reel_job["status"], "downloading")
        self.assertEqual(self.post.reel_job["id"], "video_test")

    def test_editor_plan_save_redirect_and_stale_protection(self):
        self.assertContains(
            self.client.get(reverse("social:reel_new")), "Plan a creative Reel"
        )
        with patch(
            "apps.social.reel_views.plan_reel",
            return_value={
                "storyboard": STORY,
                "facebook": "A warm reading routine for tonight.",
                "instagram": "Read together tonight.",
            },
        ):
            response = self.client.post(
                reverse("social:reel_new"),
                {"action": "plan", "brief": self.post.brief, "post_to_facebook": "on"},
            )
        self.assertEqual(response.status_code, 302)
        post = SocialPost.objects.latest("pk")
        self.assertTrue(post.is_reel)
        self.assertContains(self.client.get(response.url), STORY)
        response = self.client.post(
            response.url,
            {
                "brief": "A changed reading routine",
                "action": "save",
                "revision": "2000-01-01T00:00:00Z",
            },
        )
        self.assertContains(response, "This draft changed")
        self.assertRedirects(
            self.client.get(reverse("social:edit", args=[self.post.pk])),
            reverse("social:reel_edit", args=[self.post.pk]),
        )

    def test_draft_permissions_and_video_ranges(self):
        self.ready()
        url = reverse("social:video", args=[self.post.pk])
        response = self.client.get(url, HTTP_RANGE="bytes=4-7")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"ftyp")
        self.assertEqual(
            self.client.get(url, HTTP_RANGE="bytes=99999-").status_code, 416
        )
        from apps.social.reel_views import video_url

        signed = video_url(self.post)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(signed).status_code, 200)
        self.assertEqual(self.client.get(url + "?t=bad").status_code, 404)
        self.assertEqual(
            self.client.post(
                reverse("social:reel_generate", args=[self.post.pk]),
                {"action": "start"},
            ).status_code,
            302,
        )

    def test_unfinished_reel_cannot_schedule_and_ready_needs_no_photo(self):
        with self.assertRaisesMessage(SocialError, "Finish generating"):
            require_post_content(self.post)
        self.ready()
        require_post_content(self.post)

    def test_instagram_processing_resumes_same_container_and_preserves_success(self):
        self.ready()
        account = self.account("instagram")
        with (
            patch(
                "apps.social.reel_publishing._graph",
                side_effect=[{"id": "222"}, {"status_code": "IN_PROGRESS"}],
            ) as graph,
            self.assertRaisesMessage(SocialError, "still processing"),
        ):
            publish_reel(self.post, account)
        self.assertEqual(graph.call_args_list[0].kwargs["data"]["media_type"], "REELS")
        with patch(
            "apps.social.reel_publishing._graph",
            side_effect=[
                {"status_code": "FINISHED"},
                {"id": "333"},
                SocialError("lookup failed"),
            ],
        ) as graph:
            result = publish_reel(self.post, account)
        self.assertEqual(result["external_id"], "333")
        self.assertFalse(
            any(call.args[1].endswith("/media") for call in graph.call_args_list)
        )
        self.post.refresh_from_db()
        with patch("apps.social.reel_publishing._graph") as graph:
            self.assertEqual(publish_reel(self.post, account)["external_id"], "333")
        graph.assert_not_called()

    def test_ambiguous_instagram_publish_never_submits_twice(self):
        self.ready()
        account = self.account("instagram")
        with (
            patch(
                "apps.social.reel_publishing._graph",
                side_effect=[
                    {"id": "222"},
                    {"status_code": "FINISHED"},
                    SocialError("timeout"),
                ],
            ),
            self.assertRaises(SocialError),
        ):
            publish_reel(self.post, account)
        self.post.refresh_from_db()
        with (
            patch("apps.social.reel_publishing._graph") as graph,
            self.assertRaisesMessage(SocialError, "not confirmed publication"),
        ):
            publish_reel(self.post, account)
        graph.assert_not_called()

    def test_facebook_waits_for_published_phase_and_reuses_video(self):
        self.ready()
        account = self.account("facebook")
        with (
            patch(
                "apps.social.reel_publishing._graph",
                side_effect=[
                    {"video_id": "444"},
                    {"success": True},
                    {"success": True},
                    {"status": {"publishing_phase": {"status": "in_progress"}}},
                ],
            ),
            self.assertRaisesMessage(SocialError, "not confirmed"),
        ):
            publish_reel(self.post, account)
        self.post.refresh_from_db()
        with patch(
            "apps.social.reel_publishing._graph",
            return_value={"status": {"publishing_phase": {"status": "complete"}}},
        ) as graph:
            self.assertEqual(publish_reel(self.post, account)["external_id"], "444")
        self.assertEqual(graph.call_count, 1)
        self.assertEqual(graph.call_args.args[0], "GET")

    def test_existing_publisher_routes_reels_to_both_accounts(self):
        self.ready()
        self.account("facebook")
        self.account("instagram")
        with patch(
            "apps.social.reel_publishing.publish_reel",
            return_value={"external_id": "777", "permalink": ""},
        ) as publish:
            result = publish_post(self.post)
        self.assertEqual(result.status, "posted")
        self.assertEqual(publish.call_count, 2)
        self.assertEqual(result.publications.count(), 2)

    def test_video_transport_rejects_auth_quota_and_network_errors(self):
        for status in [401, 403, 429, 500, 302]:
            response = MagicMock(status_code=status)
            with (
                self.subTest(status=status),
                patch("apps.social.reels.requests.request", return_value=response),
                self.assertRaises(SocialError),
            ):
                _request("GET", "/video_test")
            response.close.assert_called_once()
        with (
            patch("apps.social.reels.requests.request", side_effect=requests.Timeout),
            self.assertRaises(SocialError),
        ):
            _request("GET", "/video_test")

    def test_invalid_job_response_is_held_without_duplicate_generation(self):
        with (
            patch("apps.social.reels._request") as call,
            self.assertRaises(SocialError),
        ):
            call.return_value.json.return_value = {"id": "../../bad"}
            start_reel(self.post.pk)
        self.post.refresh_from_db()
        self.assertEqual(self.post.reel_job["status"], "uncertain")

    def test_download_rejects_non_mp4_and_oversized_response(self):
        for chunks in [[b"not a video"], [b"x" * (MAX_VIDEO_BYTES + 1)]]:
            response = MagicMock()
            response.__enter__.return_value = response
            response.iter_content.return_value = iter(chunks)
            with (
                patch("apps.social.reels._request", return_value=response),
                self.assertRaises(SocialError),
            ):
                _download("video_test")

    def test_non_admin_cannot_generate_and_csrf_is_enforced(self):
        user = CustomUser.objects.create_user(
            username="ordinary-user", email="ordinary@example.com"
        )
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("social:reel_new")).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("social:reel_generate", args=[self.post.pk]),
                {"action": "start"},
            ).status_code,
            403,
        )
        from django.test import Client

        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.user)
        self.assertEqual(
            secured.post(
                reverse("social:reel_generate", args=[self.post.pk]),
                {"action": "start"},
            ).status_code,
            403,
        )

    def test_failed_provider_render_can_be_revised_and_scheduled_edits_are_blocked(
        self,
    ):
        self.post.reel_job = {"id": "video_test", "status": "failed"}
        self.post.save()
        response = self.client.post(
            reverse("social:reel_edit", args=[self.post.pk]),
            {
                "revision": self.post.updated_at.isoformat(),
                "brief": self.post.brief,
                "reel_prompt": STORY + " Camera pans slowly.",
                "facebook_caption": self.post.facebook_caption,
                "instagram_caption": self.post.instagram_caption,
                "post_to_facebook": "on",
                "action": "save",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.reel_job, {})
        self.post.status = "scheduled"
        self.post.save()
        with (
            patch("apps.social.reels._request") as call,
            self.assertRaises(SocialError),
        ):
            start_reel(self.post.pk)
        call.assert_not_called()

    def test_explicit_provider_rejection_allows_retry_after_fixing_access(self):
        with (
            patch("apps.social.reels._request", side_effect=VideoRejected("quota")),
            self.assertRaisesMessage(SocialError, "quota"),
        ):
            start_reel(self.post.pk)
        self.post.refresh_from_db()
        self.assertEqual(self.post.reel_job["status"], "failed")

    def test_active_video_cannot_be_deleted_or_opened_in_image_designer(self):
        self.post.reel_job = {"id": "video_test", "status": "queued"}
        self.post.save()
        with self.assertRaisesMessage(SocialError, "Wait for the video render"):
            delete_unpublished_post(self.post.pk, actor=self.user)
        self.assertRedirects(
            self.client.get(reverse("social:post_design", args=[self.post.pk])),
            reverse("social:reel_edit", args=[self.post.pk]),
        )
