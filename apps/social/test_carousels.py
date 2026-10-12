from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.social.carousel_ai import generate_next_slide, plan_carousel
from apps.social.crypto import encrypt_text
from apps.social.exceptions import SocialError
from apps.social.media import normalize_photo, save_slides, set_cover
from apps.social.meta import (
    absolute_image_url,
    carousel_image_urls,
    publish_facebook,
    publish_instagram,
)
from apps.social.models import SocialAccount, SocialPost, SocialPublication
from apps.social.services import publish_post, require_post_content
from apps.social.tests import META
from apps.users.models import CustomUser


def picture(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (40, 60), color).save(buffer, "PNG")
    return buffer.getvalue()


def upload(name: str = "test.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, picture(), content_type="image/png")


@override_settings(**META)
class CarouselTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.admin = CustomUser.objects.create_user(
            username="carousel-admin", role=CustomUser.Role.SUPER_ADMIN
        )

    def setUp(self) -> None:
        self.client.force_login(self.admin)
        self.post = SocialPost.objects.create(
            facebook_caption="A useful reading routine.",
            instagram_caption="A useful reading routine.",
            created_by=self.admin,
        )
        self.url = reverse("social:edit", args=[self.post.pk])
        self.data = {
            "mode": "manual",
            "caption": self.post.facebook_caption,
            "post_to_facebook": "on",
            "post_to_instagram": "on",
            "action": "save",
        }

    def attach(self) -> None:
        photos = [
            normalize_photo(picture(color), f"{color}.png")
            for color in ("red", "blue", "green")
        ]
        set_cover(self.post, photos)
        self.post.save()
        save_slides(self.post, photos)

    def account(self, network: str, *, via_facebook: bool = False) -> SocialAccount:
        return SocialAccount.objects.create(
            network=network,
            status="connected",
            external_id=f"{network}-account",
            encrypted_token=encrypt_text("test-token"),
            via_facebook=via_facebook,
        )

    def test_upload_reorder_remove_and_reload_preserve_publishing_order(self) -> None:
        response = self.client.post(
            self.url,
            {
                **self.data,
                "photo_order": json.dumps(["upload:1", "upload:0"]),
                "images": [upload("first.png"), upload("second.png")],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.image_name, "second.jpg")
        slide = self.post.slides.get()
        self.assertEqual(slide.image_name, "first.jpg")
        with Image.open(BytesIO(bytes(slide.image_data))) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (1080, 1080))
        response = self.client.get(self.url)
        self.assertContains(response, "second.jpg")
        self.assertContains(response, "first.jpg")
        response = self.client.post(
            self.url, {**self.data, "photo_order": json.dumps([str(slide.pk), "cover"])}
        )
        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.image_name, "first.jpg")
        self.assertEqual(self.post.slides.get().image_name, "second.jpg")
        self.client.post(self.url, {**self.data, "photo_order": "[]"})
        self.post.refresh_from_db()
        self.assertFalse(self.post.has_image)
        self.assertFalse(self.post.slides.exists())

    def test_invalid_uploads_or_foreign_ids_cannot_partially_save(self) -> None:
        self.attach()
        other = SocialPost.objects.create()
        foreign = other.slides.create(
            position=1, image_data=picture(), image_name="foreign.jpg"
        )
        for order, uploads in [
            ([str(foreign.pk)], []),
            (["cover", "cover"], []),
            (
                ["upload:0"],
                [
                    SimpleUploadedFile(
                        "bad.jpg", b"not a photo", content_type="image/jpeg"
                    )
                ],
            ),
            ([f"upload:{i}" for i in range(11)], [upload() for _ in range(11)]),
        ]:
            with self.subTest(order=order):
                response = self.client.post(
                    self.url,
                    {
                        **self.data,
                        "caption": "Must not save",
                        "photo_order": json.dumps(order),
                        "images": uploads,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.post.refresh_from_db()
                self.assertEqual(
                    self.post.facebook_caption, "A useful reading routine."
                )
                self.assertEqual(self.post.slides.count(), 2)

    def test_legacy_single_upload_replaces_entire_carousel(self) -> None:
        self.attach()
        self.client.post(self.url, {**self.data, "image": upload("replacement.png")})
        self.post.refresh_from_db()
        self.assertEqual(self.post.image_name, "replacement.png")
        self.assertFalse(self.post.slides.exists())

    def test_signed_slide_urls_are_bound_to_post_slide_and_expiry(self) -> None:
        self.attach()
        urls = carousel_image_urls(self.post, absolute_image_url(self.post))
        self.assertEqual(len(urls), 3)
        path = urlparse(urls[1])
        self.client.logout()
        self.assertEqual(self.client.get(path.path).status_code, 404)
        self.assertEqual(self.client.get(path.path + "?" + path.query).status_code, 200)
        other_path = urlparse(urls[2]).path
        self.assertEqual(
            self.client.get(other_path + "?" + path.query).status_code, 404
        )
        self.assertEqual(self.client.get(path.path + "?t=invalid").status_code, 404)
        with patch("django.core.signing.time.time", return_value=9999999999):
            self.assertEqual(
                self.client.get(path.path + "?" + path.query).status_code, 404
            )
        self.assertTrue(parse_qs(path.query)["t"])

    def test_instagram_publishes_only_ready_parent_in_slide_order_for_both_logins(
        self,
    ) -> None:
        account = self.account("instagram")
        for via_facebook in [False, True]:
            account.via_facebook = via_facebook
            with (
                self.subTest(via_facebook=via_facebook),
                patch(
                    "apps.social.meta._graph",
                    side_effect=[
                        {"id": "child1"},
                        {"status_code": "FINISHED"},
                        {"id": "child2"},
                        {"status_code": "FINISHED"},
                        {"id": "parent"},
                        {"status_code": "FINISHED"},
                        {"id": "published"},
                        {"permalink": "https://instagram.com/p/test"},
                    ],
                ) as graph,
            ):
                result = publish_instagram(
                    self.post, account, image_url="one", image_urls=["one", "two"]
                )
                self.assertEqual(result["external_id"], "published")
                calls = graph.call_args_list
                self.assertEqual(
                    calls[0].kwargs["data"],
                    {"image_url": "one", "is_carousel_item": "true"},
                )
                self.assertEqual(calls[2].kwargs["data"]["image_url"], "two")
                self.assertEqual(calls[4].kwargs["data"]["children"], "child1,child2")
                self.assertEqual(calls[4].kwargs["data"]["media_type"], "CAROUSEL")
                self.assertEqual(calls[6].kwargs["data"], {"creation_id": "parent"})
                self.assertIn(
                    "graph.facebook.com" if via_facebook else "graph.instagram.com",
                    calls[0].args[1],
                )

    def test_instagram_child_failure_or_timeout_never_publishes_parent(self) -> None:
        account = self.account("instagram")
        for status in ["ERROR", "EXPIRED", "IN_PROGRESS"]:
            replies = [{"id": "child"}] + [{"status_code": status}] * 5
            with (
                self.subTest(status=status),
                patch("apps.social.meta.time.sleep"),
                patch("apps.social.meta._graph", side_effect=replies) as graph,
            ):
                with self.assertRaises(SocialError):
                    publish_instagram(
                        self.post, account, image_url="one", image_urls=["one", "two"]
                    )
                self.assertFalse(
                    any(
                        "media_publish" in call.args[1] for call in graph.call_args_list
                    )
                )
                self.assertFalse(
                    any(
                        call.kwargs.get("data", {}).get("media_type") == "CAROUSEL"
                        for call in graph.call_args_list
                    )
                )

    def test_facebook_uploads_unpublished_then_attaches_in_order(self) -> None:
        account = self.account("facebook")
        with patch(
            "apps.social.meta._graph",
            side_effect=[
                {"id": "photo1"},
                {"id": "photo2"},
                {"id": "live"},
                {"permalink_url": "https://facebook.com/live"},
            ],
        ) as graph:
            result = publish_facebook(
                self.post, account, image_url="one", image_urls=["one", "two"]
            )
        self.assertEqual(result["external_id"], "live")
        self.assertEqual(
            graph.call_args_list[0].kwargs["data"], {"url": "one", "published": "false"}
        )
        self.assertEqual(
            json.loads(graph.call_args_list[2].kwargs["data"]["attached_media"]),
            [{"media_fbid": "photo1"}, {"media_fbid": "photo2"}],
        )
        with patch(
            "apps.social.meta._graph", side_effect=[{"id": "photo1"}, {}]
        ) as graph:
            with self.assertRaises(SocialError):
                publish_facebook(
                    self.post, account, image_url="one", image_urls=["one", "two"]
                )
            self.assertEqual(graph.call_count, 2)

    def test_permalink_failure_does_not_repeat_confirmed_instagram_publication(
        self,
    ) -> None:
        account = self.account("instagram")
        with patch(
            "apps.social.meta._graph",
            side_effect=[
                {"id": "container"},
                {"status_code": "FINISHED"},
                {"id": "live"},
                SocialError("timeout"),
            ],
        ):
            result = publish_instagram(self.post, account, image_url="one")
        self.assertEqual(result, {"external_id": "live", "permalink": ""})

    def test_retry_skips_successful_network_and_passes_all_photos(self) -> None:
        self.attach()
        self.account("instagram")
        SocialPublication.objects.create(
            post=self.post,
            network="facebook",
            status="published",
            external_id="already-live",
        )
        with (
            patch("apps.social.meta.publish_facebook") as facebook,
            patch(
                "apps.social.meta.publish_instagram",
                return_value={"external_id": "ig-live", "permalink": ""},
            ) as instagram,
        ):
            publish_post(self.post)
        facebook.assert_not_called()
        self.assertEqual(len(instagram.call_args.kwargs["image_urls"]), 3)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, "posted")

    def test_ai_plan_validation_and_no_image_requests_during_planning(self) -> None:
        valid = {
            "facebook": "Make reading a warm daily habit.",
            "instagram": "A warm reading habit.",
            "slides": [
                "A closed book on a sunny table",
                "Blank cards beside a teal reading lamp",
            ],
        }
        with (
            patch("apps.social.carousel_ai._write_json", return_value=valid),
            patch("apps.social.carousel_ai.generate_image") as generate,
        ):
            plan = plan_carousel(
                subject="Build a daily reading habit",
                count=2,
                audience="families",
                tone="warm",
            )
        self.assertEqual(plan["completed"], 0)
        generate.assert_not_called()
        for invalid in [
            {**valid, "slides": ["short"]},
            {**valid, "slides": [valid["slides"][0]] * 2},
            {**valid, "facebook": "Guaranteed reading results for everyone"},
        ]:
            with (
                self.subTest(invalid=invalid),
                patch("apps.social.carousel_ai._write_json", return_value=invalid),
                self.assertRaises(SocialError),
            ):
                plan_carousel(
                    subject="Build a daily reading habit",
                    count=2,
                    audience="families",
                    tone="warm",
                )

    def test_ai_generation_resumes_without_replacing_completed_slides(self) -> None:
        self.attach()
        self.post.carousel_plan = {
            "slides": ["A sunny reading corner", "A warm reading lamp"],
            "completed": 0,
        }
        self.post.save()
        with (
            patch(
                "apps.social.carousel_ai.generate_image",
                side_effect=SocialError("quota"),
            ),
            self.assertRaises(SocialError),
        ):
            generate_next_slide(self.post.pk)
        self.post.refresh_from_db()
        self.assertEqual(self.post.image_name, "red.jpg")
        self.assertFalse(self.post.carousel_plan["token"])
        with patch(
            "apps.social.carousel_ai.generate_image",
            return_value=(picture(), "image/png"),
        ) as generate:
            self.assertEqual(generate_next_slide(self.post.pk), (1, 2))
            self.post.refresh_from_db()
            cover = bytes(self.post.image_data)
            self.assertFalse(self.post.slides.exists())
            with self.assertRaises(SocialError):
                require_post_content(self.post)
            self.assertEqual(generate_next_slide(self.post.pk), (2, 2))
            self.assertEqual(generate_next_slide(self.post.pk), (2, 2))
            self.assertEqual(generate.call_count, 2)
        self.post.refresh_from_db()
        self.assertEqual(bytes(self.post.image_data), cover)
        self.assertEqual(self.post.slides.count(), 1)
        require_post_content(self.post)

    def test_generation_endpoint_permissions_lease_and_edit_race(self) -> None:
        self.post.carousel_plan = {
            "slides": ["A sunny reading corner", "A warm reading lamp"],
            "completed": 0,
        }
        self.post.save()
        url = reverse("social:carousel_generate", args=[self.post.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)
        self.client.force_login(self.admin)

        def competing_request(*, subject: str) -> tuple[bytes, str]:
            with self.assertRaises(SocialError):
                generate_next_slide(self.post.pk)
            response = self.client.post(self.url, self.data)
            self.assertContains(response, "being generated")
            return picture(), "image/png"

        with patch(
            "apps.social.carousel_ai.generate_image", side_effect=competing_request
        ):
            self.assertEqual(self.client.post(url).json(), {"completed": 1, "total": 2})

    def test_plan_action_saves_captions_and_blocks_scheduling_until_complete(
        self,
    ) -> None:
        plan = {
            "slides": ["A sunny reading corner", "A warm reading lamp"],
            "completed": 0,
            "facebook": "A new Facebook caption",
            "instagram": "A new Instagram caption",
        }
        with patch("apps.social.views.plan_carousel", return_value=plan):
            self.assertEqual(
                self.client.post(
                    self.url,
                    {**self.data, "action": "plan_carousel", "slide_count": "2"},
                ).status_code,
                302,
            )
        self.post.refresh_from_db()
        self.assertEqual(self.post.facebook_caption, "A new Facebook caption")
        response = self.client.post(self.url, {**self.data, "action": "schedule"})
        self.assertContains(response, "Finish generating")
        response = self.client.post(
            self.url, {**self.data, "action": "discard_carousel"}
        )
        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.carousel_plan, {})

    def test_rendered_revision_saves_and_stale_revision_cannot_overwrite_generated_slide(
        self,
    ) -> None:
        from html.parser import HTMLParser

        class RevisionParser(HTMLParser):
            revision = ""

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == "input" and values.get("name") == "revision":
                    self.revision = values["value"]

        response = self.client.get(self.url)
        parser = RevisionParser()
        parser.feed(response.content.decode())
        revision = parser.revision
        self.assertEqual(
            self.client.post(self.url, {**self.data, "revision": revision}).status_code,
            302,
        )
        self.post.refresh_from_db()
        self.post.facebook_caption = "Newer edits must stay"
        self.post.save()
        response = self.client.post(self.url, {**self.data, "revision": revision})
        self.assertContains(response, "changed in another window")
        self.post.refresh_from_db()
        self.assertEqual(self.post.facebook_caption, "Newer edits must stay")

    def test_scheduled_post_cannot_save_removal_of_its_required_photos(self) -> None:
        self.attach()
        self.post.status = SocialPost.Status.SCHEDULED
        self.post.save()
        response = self.client.post(self.url, {**self.data, "photo_order": "[]"})
        self.assertContains(response, "Instagram needs a photo")
        self.post.refresh_from_db()
        self.assertTrue(self.post.has_image)
        self.assertEqual(self.post.slides.count(), 2)

    def test_transparent_upload_is_flattened_and_metadata_removed(self) -> None:
        source = BytesIO()
        Image.new("RGBA", (20, 20), (255, 0, 0, 0)).save(source, "PNG")
        photo = normalize_photo(source.getvalue(), "transparent.png")
        with Image.open(BytesIO(photo.data)) as image:
            self.assertEqual(image.mode, "RGB")
            self.assertFalse(image.getexif())
            red, green, blue = image.getpixel((540, 540))
            self.assertLess(abs(red - 247), 3)
            self.assertLess(abs(green - 242), 3)
            self.assertLess(abs(blue - 234), 3)

    def test_scheduled_carousel_uses_same_order_for_both_networks(self) -> None:
        from django.utils import timezone

        from apps.social.services import publish_due, schedule_post

        self.attach()
        self.account("facebook")
        self.account("instagram")
        schedule_post(self.post, timezone.now(), actor=self.admin)
        with (
            patch(
                "apps.social.meta.publish_facebook",
                return_value={"external_id": "fb", "permalink": ""},
            ) as facebook,
            patch(
                "apps.social.meta.publish_instagram",
                return_value={"external_id": "ig", "permalink": ""},
            ) as instagram,
        ):
            self.assertEqual(publish_due(), 1)
        self.assertEqual(
            facebook.call_args.kwargs["image_urls"],
            instagram.call_args.kwargs["image_urls"],
        )
        self.assertEqual(len(facebook.call_args.kwargs["image_urls"]), 3)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, SocialPost.Status.POSTED)

    def test_carousel_edit_retires_weekly_approval_and_prevents_auto_scheduling(
        self,
    ) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from apps.social.models import ContentPlan, ContentWeek
        from apps.social.planner import _schedule_ready

        plan = ContentPlan.objects.create(
            mode=ContentPlan.Mode.AUTOMATIC, updated_by=self.admin
        )
        week = ContentWeek.objects.create(
            week_of=timezone.now().date(),
            planned_at=timezone.now() + timedelta(days=2),
            pillar="family_support",
            post=self.post,
            status=ContentWeek.Status.READY,
            review_passed=True,
        )
        response = self.client.post(
            self.url,
            {
                **self.data,
                "photo_order": '["upload:0","upload:1"]',
                "images": [upload("one.png"), upload("two.png")],
            },
        )
        self.assertEqual(response.status_code, 302)
        week.refresh_from_db()
        self.assertFalse(week.review_passed)
        self.assertEqual(week.status, ContentWeek.Status.SKIPPED)
        _schedule_ready(plan)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, SocialPost.Status.DRAFT)
        week.status, week.review_passed, week.auto_scheduled = (
            ContentWeek.Status.READY,
            True,
            True,
        )
        week.save()
        self.post.status = SocialPost.Status.SCHEDULED
        self.post.scheduled_at = week.planned_at
        self.post.save()
        self.client.post(
            self.url,
            {
                **self.data,
                "photo_order": '["upload:0","upload:1"]',
                "images": [upload("three.png"), upload("four.png")],
            },
        )
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, SocialPost.Status.DRAFT)
        self.assertIsNone(self.post.scheduled_at)

    def test_ai_carousel_plan_retires_weekly_approval_before_generation(self) -> None:
        from django.utils import timezone

        from apps.social.models import ContentPlan, ContentWeek

        ContentPlan.objects.create(updated_by=self.admin)
        week = ContentWeek.objects.create(
            week_of=timezone.now().date(),
            planned_at=timezone.now(),
            pillar="family_support",
            post=self.post,
            status=ContentWeek.Status.READY,
            review_passed=True,
        )
        plan = {
            "slides": ["A sunny reading corner", "A warm reading lamp"],
            "completed": 0,
            "facebook": "A new Facebook caption",
            "instagram": "A new Instagram caption",
        }
        with patch("apps.social.views.plan_carousel", return_value=plan):
            response = self.client.post(
                self.url, {**self.data, "action": "plan_carousel", "slide_count": "2"}
            )
        self.assertEqual(response.status_code, 302)
        week.refresh_from_db()
        self.assertFalse(week.review_passed)
        self.assertEqual(week.status, ContentWeek.Status.SKIPPED)
