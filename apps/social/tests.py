import base64
import json
from datetime import timedelta
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import requests
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.social.crypto import encrypt_text
from apps.social.drafts import draft_captions
from apps.social.exceptions import SocialError
from apps.social.meta import absolute_image_url, image_signature
from apps.social.models import SocialAccount, SocialPost, SocialPublication
from apps.social.services import EASTERN, publish_due
from apps.users.models import CustomUser


def image_bytes():
    output = BytesIO()
    Image.new("RGB", (32, 32), "#1A7A7A").save(output, format="PNG")
    return output.getvalue()


KEY = Fernet.generate_key().decode()
META = {
    "SOCIAL_META_APP_ID": "app-id",
    "SOCIAL_META_APP_SECRET": "app-secret",
    "SOCIAL_FACEBOOK_REDIRECT_URI": "https://example.com/portal/marketing/settings/facebook/callback/",
    "SOCIAL_INSTAGRAM_REDIRECT_URI": "https://example.com/portal/marketing/settings/instagram/callback/",
    "SOCIAL_PUBLIC_BASE_URL": "https://example.com",
    "CRM_EMAIL_ENCRYPTION_KEYS": [KEY],
    "ENABLE_DEMO_ACCESS": False,
    "DEBUG": True,
}


@override_settings(**META)
class SocialMediaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_user(
            username="social-admin",
            email="social-admin@example.com",
            password="test-pass-123",
            role=CustomUser.Role.SUPER_ADMIN,
            first_name="Jamie",
        )
        cls.school = CustomUser.objects.create_user(
            username="social-school",
            email="social-school@example.com",
            password="test-pass-123",
            role=CustomUser.Role.SCHOOL_ADMIN,
        )
        cls.teacher = CustomUser.objects.create_user(
            username="social-teacher",
            email="social-teacher@example.com",
            password="test-pass-123",
            role=CustomUser.Role.TEACHER,
        )

    def _future(self, days=10, hour=15, minute=0):
        local = (timezone.now() + timedelta(days=days)).astimezone(EASTERN)
        return local.date().isoformat(), f"{hour:02d}:{minute:02d}"

    def _connect_facebook(self, *, instagram=False):
        SocialAccount.objects.create(
            network=SocialAccount.Network.FACEBOOK,
            status=SocialAccount.Status.CONNECTED,
            external_id="page-1",
            display_name="ClearCode Reading",
            encrypted_token=encrypt_text("page-token"),
            connected_by=self.admin,
            connected_at=timezone.now(),
        )
        if instagram:
            SocialAccount.objects.create(
                network=SocialAccount.Network.INSTAGRAM,
                status=SocialAccount.Status.CONNECTED,
                external_id="ig-1",
                display_name="@clearcodereading",
                username="clearcodereading",
                encrypted_token=encrypt_text("page-token"),
                via_facebook=True,
                connected_by=self.admin,
                connected_at=timezone.now(),
            )

    def test_school_admin_and_teacher_cannot_open_social_media(self):
        self.client.force_login(self.school)
        response = self.client.get(reverse("social:queue"))
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse("social:settings")).status_code, 403)

    def test_anonymous_visitors_are_sent_to_login(self):
        response = self.client.get(reverse("social:queue"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_brief_creates_two_captions_and_refuses_scores(self):
        facebook, instagram = draft_captions(
            brief="We published a parent article about a calmer ten-minute reading routine.",
            audience="families",
            tone="warm",
            link="https://clearcodereading.com/blog/calmer",
        )
        self.assertIn("https://clearcodereading.com/blog/calmer", facebook)
        self.assertIn("#ClearCodeReading", instagram)
        self.assertNotIn("https://", instagram)
        with self.assertRaises(SocialError):
            draft_captions(brief="This child scored 12 on the inventory today.", audience="families", tone="warm", link="")

    def test_composer_saves_a_draft_from_a_brief(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("social:new"),
            {
                "mode": "brief",
                "action": "draft",
                "brief": "We published a parent article about a calmer ten-minute reading routine.",
                "audience": "families",
                "tone": "warm",
                "link_url": "https://clearcodereading.com/blog/calmer",
                "post_to_facebook": "on",
                "post_to_instagram": "on",
            },
        )
        post = SocialPost.objects.get()
        self.assertRedirects(response, f"{reverse('social:edit', kwargs={'pk': post.pk})}?mode=brief&return_to=drafts")
        self.assertEqual(post.status, SocialPost.Status.DRAFT)
        self.assertIn("calmer", post.facebook_caption)
        self.assertIn("#ReadingAtHome", post.instagram_caption)

    def test_schedule_change_date_and_cancel(self):
        self._connect_facebook()
        self.client.force_login(self.admin)
        post = SocialPost.objects.create(
            facebook_caption="A calmer reading homework routine.",
            instagram_caption="",
            post_to_facebook=True,
            post_to_instagram=False,
            status=SocialPost.Status.DRAFT,
            created_by=self.admin,
        )
        first_day, first_time = self._future(10, 9, 0)
        response = self.client.post(reverse("social:schedule", kwargs={"pk": post.pk}), {"date": first_day, "time": first_time})
        self.assertRedirects(response, f"{reverse('social:queue')}?tab=scheduled")
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.SCHEDULED)
        scheduled = self.client.get(reverse("social:queue"))
        self.assertContains(scheduled, "Change date")
        self.assertContains(scheduled, "A calmer reading homework routine.")

        next_day, next_time = self._future(12, 16, 30)
        self.client.post(reverse("social:schedule", kwargs={"pk": post.pk}), {"date": next_day, "time": next_time})
        post.refresh_from_db()
        local = timezone.localtime(post.scheduled_at, EASTERN)
        self.assertEqual(local.date().isoformat(), next_day)
        self.assertEqual(local.hour, 16)

        past = (timezone.now() - timedelta(days=1)).astimezone(EASTERN)
        rejected = self.client.post(
            reverse("social:schedule", kwargs={"pk": post.pk}),
            {"date": past.date().isoformat(), "time": "09:00"},
            follow=True,
        )
        self.assertContains(rejected, "Choose a time in the future")
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.SCHEDULED)

        self.client.post(reverse("social:cancel", kwargs={"pk": post.pk}))
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.DRAFT)
        self.assertIsNone(post.scheduled_at)
        drafts = self.client.get(f"{reverse('social:queue')}?tab=drafts")
        self.assertContains(drafts, "A calmer reading homework routine.")

    def test_posted_history_links_to_the_live_post(self):
        self.client.force_login(self.admin)
        post = SocialPost.objects.create(
            facebook_caption="A 10-minute routine for reading homework.",
            post_to_facebook=True,
            post_to_instagram=False,
            status=SocialPost.Status.POSTED,
            created_by=self.admin,
        )
        SocialPublication.objects.create(
            post=post,
            network=SocialAccount.Network.FACEBOOK,
            status=SocialPublication.Status.PUBLISHED,
            external_id="99",
            permalink="https://www.facebook.com/99",
            published_at=timezone.now(),
        )
        response = self.client.get(f"{reverse('social:queue')}?tab=posted")
        self.assertContains(response, "View on Facebook")
        self.assertContains(response, "https://www.facebook.com/99")
        self.assertNotContains(response, "Change date")

    def test_due_post_publishes_and_a_partial_failure_needs_attention(self):
        self._connect_facebook(instagram=True)
        ready = SocialPost.objects.create(
            facebook_caption="Listen for three sounds.",
            post_to_facebook=True,
            post_to_instagram=False,
            status=SocialPost.Status.SCHEDULED,
            scheduled_at=timezone.now() - timedelta(minutes=1),
            created_by=self.admin,
        )
        with patch("apps.social.meta.publish_facebook", return_value={"external_id": "fb-1", "permalink": "https://facebook.com/fb-1"}):
            self.assertEqual(publish_due(), 1)
        ready.refresh_from_db()
        self.assertEqual(ready.status, SocialPost.Status.POSTED)
        self.assertEqual(ready.publications.get().permalink, "https://facebook.com/fb-1")

        mixed = SocialPost.objects.create(
            facebook_caption="Both networks.",
            instagram_caption="Both networks.",
            post_to_facebook=True,
            post_to_instagram=True,
            image_data=b"image-bytes",
            image_content_type="image/jpeg",
            image_name="photo.jpg",
            status=SocialPost.Status.SCHEDULED,
            scheduled_at=timezone.now() - timedelta(minutes=1),
            created_by=self.admin,
        )
        with (
            patch("apps.social.meta.publish_facebook", return_value={"external_id": "fb-2", "permalink": "https://facebook.com/fb-2"}),
            patch("apps.social.meta.publish_instagram", side_effect=SocialError("Instagram could not prepare the photo.")),
        ):
            publish_due()
        mixed.refresh_from_db()
        self.assertEqual(mixed.status, SocialPost.Status.ATTENTION)
        self.assertEqual(mixed.publications.filter(status=SocialPublication.Status.PUBLISHED).count(), 1)
        self.client.force_login(self.admin)
        attention = self.client.get(f"{reverse('social:queue')}?tab=attention")
        self.assertContains(attention, "Both networks.")

    def test_publishing_post_cannot_be_rescheduled(self):
        self._connect_facebook()
        post = SocialPost.objects.create(
            facebook_caption="Already sending.",
            post_to_facebook=True,
            post_to_instagram=False,
            status=SocialPost.Status.PUBLISHING,
            scheduled_at=timezone.now(),
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("social:schedule", kwargs={"pk": post.pk}), follow=True)
        self.assertContains(response, "can no longer be rescheduled")

    def test_facebook_sign_in_connects_the_page_and_linked_instagram(self):
        self.client.force_login(self.admin)
        started = self.client.post(reverse("social:facebook_connect"))
        state = parse_qs(urlparse(started["Location"]).query)["state"][0]
        self.assertIn("facebook.com", started["Location"])
        pages = [
            {
                "id": "page-1",
                "name": "ClearCode Reading",
                "token": "secret-page-token",
                "instagram_id": "ig-9",
                "instagram_username": "clearcodereading",
            }
        ]
        with patch("apps.social.views.exchange_facebook_code", return_value=pages):
            callback = self.client.get(reverse("social:facebook_callback"), {"state": state, "code": "abc"})
        self.assertRedirects(callback, reverse("social:settings"))
        facebook = SocialAccount.objects.get(network=SocialAccount.Network.FACEBOOK)
        instagram = SocialAccount.objects.get(network=SocialAccount.Network.INSTAGRAM)
        self.assertEqual(facebook.display_name, "ClearCode Reading")
        self.assertTrue(facebook.is_connected)
        self.assertEqual(instagram.username, "clearcodereading")
        self.assertTrue(instagram.via_facebook)
        self.assertNotIn("secret-page-token", str(facebook.encrypted_token))
        settings_page = self.client.get(reverse("social:settings"))
        self.assertContains(settings_page, "ClearCode Reading")
        self.assertContains(settings_page, "@clearcodereading")

    def test_several_pages_ask_which_one_to_keep(self):
        self.client.force_login(self.admin)
        started = self.client.post(reverse("social:facebook_connect"))
        state = parse_qs(urlparse(started["Location"]).query)["state"][0]
        pages = [
            {"id": "page-1", "name": "ClearCode Reading", "token": "token-one", "instagram_id": "", "instagram_username": ""},
            {"id": "page-2", "name": "Other Page", "token": "token-two", "instagram_id": "", "instagram_username": ""},
        ]
        with patch("apps.social.views.exchange_facebook_code", return_value=pages):
            callback = self.client.get(reverse("social:facebook_callback"), {"state": state, "code": "abc"})
        self.assertRedirects(callback, reverse("social:choose_page"))
        choice = self.client.get(reverse("social:choose_page"))
        self.assertContains(choice, "ClearCode Reading")
        self.assertNotContains(choice, "token-one")
        self.assertContains(choice, "Back to Settings")
        self.assertContains(choice, 'aria-label="Social marketing"')
        self.client.post(reverse("social:choose_page"), {"page_id": "page-1"})
        facebook = SocialAccount.objects.get(network=SocialAccount.Network.FACEBOOK)
        self.assertEqual(facebook.external_id, "page-1")
        from apps.social.services import token_for_tests

        self.assertEqual(token_for_tests(facebook), "token-one")

    def test_disconnect_leaves_a_separate_instagram_and_holds_scheduled_posts(self):
        self._connect_facebook()
        SocialAccount.objects.create(
            network=SocialAccount.Network.INSTAGRAM,
            status=SocialAccount.Status.CONNECTED,
            external_id="ig-own",
            display_name="@clearcodereading",
            username="clearcodereading",
            encrypted_token=encrypt_text("ig-token"),
            via_facebook=False,
            connected_by=self.admin,
        )
        waiting = SocialPost.objects.create(
            facebook_caption="Waiting on Facebook.",
            post_to_facebook=True,
            post_to_instagram=False,
            status=SocialPost.Status.SCHEDULED,
            scheduled_at=timezone.now() + timedelta(days=3),
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        self.client.post(reverse("social:disconnect", kwargs={"network": "facebook"}))
        self.assertEqual(SocialAccount.objects.get(network="facebook").status, SocialAccount.Status.DISCONNECTED)
        self.assertTrue(SocialAccount.objects.get(network="instagram").is_connected)
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, SocialPost.Status.ATTENTION)

    def test_signed_image_is_readable_without_a_login_and_hidden_otherwise(self):
        post = SocialPost.objects.create(
            facebook_caption="Photo post",
            post_to_facebook=True,
            post_to_instagram=False,
            image_data=b"jpeg-bytes",
            image_content_type="image/jpeg",
            image_name="photo.jpg",
            created_by=self.admin,
        )
        hidden = self.client.get(reverse("social:image", kwargs={"pk": post.pk}))
        self.assertEqual(hidden.status_code, 404)
        visible = self.client.get(reverse("social:image", kwargs={"pk": post.pk}), {"t": image_signature(post.pk)})
        self.assertEqual(visible.status_code, 200)
        self.assertEqual(visible.content, b"jpeg-bytes")
        self.assertTrue(absolute_image_url(post).startswith("https://example.com/"))

    def test_draft_with_ai_requires_the_openai_key(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("social:new"),
            {
                "mode": "brief",
                "action": "draft_ai",
                "brief": "A ten-minute reading routine for homework nights.",
                "audience": "families",
                "tone": "warm",
                "post_to_facebook": "on",
            },
            follow=True,
        )
        self.assertContains(response, "SOCIAL_OPENAI_API_KEY")
        self.assertEqual(SocialPost.objects.count(), 0)

    @override_settings(SOCIAL_OPENAI_API_KEY="test-key")
    def test_draft_with_ai_writes_captions_and_image_then_schedule_can_be_removed(self):
        self._connect_facebook(instagram=True)
        self.client.force_login(self.admin)
        with (
            patch(
                "apps.social.views.write_captions",
                return_value=(
                    "Homework can end calmly. Try one short page tonight and stop while it still feels good.",
                    "One short page tonight. Stop while it still feels good.\n\n#ClearCodeReading #ReadingAtHome",
                ),
            ),
            patch("apps.social.views.generate_image", return_value=(b"png-bytes", "image/png")),
        ):
            response = self.client.post(
                reverse("social:new"),
                {
                    "mode": "brief",
                    "action": "draft_ai",
                    "brief": "A ten-minute reading routine for homework nights.",
                    "audience": "families",
                    "tone": "warm",
                    "link_url": "https://clearcodereading.com/blog/calmer",
                    "generate_image": "on",
                    "post_to_facebook": "on",
                    "post_to_instagram": "on",
                },
            )
        post = SocialPost.objects.get()
        self.assertRedirects(response, f"{reverse('social:edit', kwargs={'pk': post.pk})}?mode=brief&return_to=drafts")
        self.assertIn("Homework can end calmly", post.facebook_caption)
        self.assertIn("#ReadingAtHome", post.instagram_caption)
        self.assertEqual(bytes(post.image_data), b"png-bytes")
        self.assertEqual(post.status, SocialPost.Status.DRAFT)

        day, clock = self._future(9, 9, 0)
        self.client.post(reverse("social:schedule", kwargs={"pk": post.pk}), {"date": day, "time": clock})
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.SCHEDULED)
        self.client.post(reverse("social:cancel", kwargs={"pk": post.pk}))
        post.refresh_from_db()
        self.assertEqual(post.status, SocialPost.Status.DRAFT)
        self.assertIsNone(post.scheduled_at)

    @override_settings(SOCIAL_OPENAI_API_KEY="test-key", SOCIAL_AI_TEXT_MODEL="gpt-4o-mini", SOCIAL_AI_IMAGE_MODEL="dall-e-3")
    def test_openai_request_migrates_retired_dalle_and_validates_image(self):
        from apps.social.ai import generate_image, write_captions

        captions = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "facebook": "Homework can stay calm when you stop after one short page.",
                                "instagram": "One short page, then stop.\n\n#ClearCodeReading #ReadingAtHome",
                            }
                        )
                    }
                }
            ]
        }
        image = {"data": [{"b64_json": base64.b64encode(image_bytes()).decode()}]}

        def respond(*_args, **kwargs):
            url = _args[0]
            response = type("Response", (), {})()
            response.status_code = 200
            response.json = lambda: captions if "chat/completions" in url else image
            payload = kwargs["json"]
            if "chat/completions" in url:
                self.assertEqual(payload["model"], "gpt-4o-mini")
            else:
                self.assertEqual(payload["model"], "gpt-image-2")
                self.assertNotIn("response_format", payload)
                self.assertEqual(payload["output_format"], "jpeg")
                self.assertIn("no people", payload["prompt"].lower())
                self.assertIn("no text", payload["prompt"].lower())
                self.assertIn("photorealistic editorial photograph", payload["prompt"])
                self.assertIn("cheerful", payload["prompt"])
                self.assertIn("No cartoons, flat illustrations", payload["prompt"])
                self.assertIn("take precedence over any style", payload["prompt"])
                self.assertNotIn("or a photograph", payload["prompt"])
            return response

        with patch("apps.social.ai.requests.post", side_effect=respond):
            facebook, instagram = write_captions(
                subject="A ten-minute reading routine for homework nights.",
                audience="families",
                tone="warm",
                link="https://clearcodereading.com/blog/calmer",
            )
            raw, content_type = generate_image(subject="A ten-minute reading routine for homework nights.")
        self.assertIn("one short page", facebook)
        self.assertIn("#ClearCodeReading", instagram)
        with Image.open(BytesIO(raw)) as decoded:
            self.assertEqual(decoded.format, "JPEG")
        self.assertEqual(content_type, "image/jpeg")

        with patch("apps.social.ai.requests.post") as post:
            with self.assertRaises(SocialError):
                write_captions(
                    subject="This child scored 12 on the inventory today.",
                    audience="families",
                    tone="warm",
                    link="",
                )
            post.assert_not_called()

    @override_settings(SOCIAL_OPENAI_API_KEY="test-key")
    def test_ideas_from_blank_subject_do_not_save_or_generate_image(self):
        self.client.force_login(self.admin)
        ideas = ["Try a calm shared reading routine.", "Explore letter sounds with household objects.", "Ask an open question about a book."]
        with patch("apps.social.views.suggest_ideas", return_value=ideas) as suggest, patch("apps.social.views.generate_image") as image:
            response = self.client.post(reverse("social:new"), {"mode": "brief", "action": "ideas", "audience": "teachers", "tone": "practical", "generate_image": "on"})
        suggest.assert_called_once_with(audience="teachers", tone="practical")
        image.assert_not_called()
        self.assertEqual(SocialPost.objects.count(), 0)
        self.assertContains(response, "Draft this idea", count=3)
        self.assertContains(response, ideas[0])

    def test_selected_idea_creates_reviewable_captions_and_image(self):
        self.client.force_login(self.admin)
        idea = "Try a calm shared reading routine."
        with patch("apps.social.views.write_captions", return_value=("A new Facebook caption to review.", "An Instagram caption to review.")) as write, patch("apps.social.views.generate_image", return_value=(b"jpeg-picture", "image/jpeg")):
            response = self.client.post(reverse("social:new"), {"mode": "brief", "selected_idea": idea, "generate_image": "on", "post_to_facebook": "on", "post_to_instagram": "on"}, follow=True)
        post = SocialPost.objects.get()
        self.assertEqual(post.brief, idea)
        self.assertEqual(write.call_args.kwargs["subject"], idea)
        self.assertEqual(post.status, SocialPost.Status.DRAFT)
        self.assertIsNone(post.scheduled_at)
        self.assertFalse(post.publications.exists())
        self.assertEqual(post.image_content_type, "image/jpeg")
        self.assertContains(response, "A new Facebook caption to review.")
        picture = self.client.get(reverse("social:image", args=[post.pk]))
        self.assertEqual(picture.content, b"jpeg-picture")
        self.assertEqual(picture["Content-Type"], "image/jpeg")

    def test_image_failure_keeps_generated_captions_and_previous_image(self):
        self.client.force_login(self.admin)
        post = SocialPost.objects.create(brief="A calm shared reading routine.", source="brief", image_data=b"old-image", image_content_type="image/jpeg", created_by=self.admin)
        with patch("apps.social.views.write_captions", return_value=("Fresh Facebook caption for review.", "Fresh Instagram caption.")), patch("apps.social.views.generate_image", side_effect=SocialError("Image service timed out.")):
            response = self.client.post(reverse("social:edit", args=[post.pk]), {"mode": "brief", "action": "draft_ai", "brief": post.brief, "generate_image": "on"}, follow=True)
        post.refresh_from_db()
        self.assertEqual(post.facebook_caption, "Fresh Facebook caption for review.")
        self.assertEqual(bytes(post.image_data), b"old-image")
        self.assertContains(response, "Image service timed out.")

    def test_manual_caption_can_generate_an_image_without_a_brief(self):
        self.client.force_login(self.admin)
        with patch("apps.social.views.generate_image", return_value=(b"jpeg-image", "image/jpeg")) as generate:
            self.client.post(reverse("social:new"), {"mode": "manual", "action": "new_image", "caption": "A calm reading routine for tonight."})
        generate.assert_called_once_with(subject="A calm reading routine for tonight.")
        self.assertEqual(SocialPost.objects.get().facebook_caption, "A calm reading routine for tonight.")

    def test_scheduled_post_cannot_receive_unreviewed_ai_content(self):
        self.client.force_login(self.admin)
        post = SocialPost.objects.create(source="brief", facebook_caption="Reviewed caption", status="scheduled", scheduled_at=timezone.now() + timedelta(days=1), created_by=self.admin)
        with patch("apps.social.views.write_captions") as write:
            response = self.client.post(reverse("social:edit", args=[post.pk]), {"mode": "brief", "action": "draft_ai", "brief": "Some fresh subject for a new post."})
        write.assert_not_called()
        post.refresh_from_db()
        self.assertEqual(post.facebook_caption, "Reviewed caption")
        self.assertContains(response, "Cancel this post")

    @override_settings(SOCIAL_OPENAI_API_KEY="test-key")
    def test_idea_provider_receives_brand_guide_and_rejects_invalid_outputs(self):
        from apps.social.ai import suggest_ideas
        ideas = ["Try a calm shared reading routine.", "Explore letter sounds with household objects.", "Ask an open question about a book."]
        for result in ({"ideas": ideas}, {"ideas": ["Only one idea"]}, {"ideas": [None, 123, {}]}, {"ideas": ["Guaranteed reading results for every child."] + ideas[1:]}):
            response = Mock(status_code=200)
            response.json.return_value = {"choices": [{"message": {"content": json.dumps(result)}}]}
            with patch("apps.social.ai.requests.post", return_value=response) as provider:
                if result == {"ideas": ideas}:
                    self.assertEqual(suggest_ideas(audience="families", tone="warm"), ideas)
                else:
                    with self.assertRaises(SocialError):
                        suggest_ideas(audience="families", tone="warm")
            prompt = provider.call_args.kwargs["json"]["messages"][0]["content"]
            self.assertIn("Unlock Reading. Unlock Everything.", prompt)
            self.assertIn("#1A7A7A", prompt)
            self.assertIn("no", prompt.lower())

    @override_settings(SOCIAL_OPENAI_API_KEY="test-key")
    def test_image_provider_failures_are_clear_and_do_not_retry_paid_requests(self):
        from apps.social.ai import generate_image
        for status, expected in [(401, "key was rejected"), (403, "model permissions"), (429, "quota"), (500, "could not be generated")]:
            with self.subTest(status=status), patch("apps.social.ai.requests.post", return_value=Mock(status_code=status)) as provider:
                with self.assertRaisesMessage(SocialError, expected):
                    generate_image(subject="A calm reading routine for tonight.")
                self.assertEqual(provider.call_count, 1)
        with patch("apps.social.ai.requests.post", side_effect=requests.Timeout), self.assertRaisesMessage(SocialError, "took too long"):
            generate_image(subject="A calm reading routine for tonight.")

    @override_settings(SOCIAL_OPENAI_API_KEY="test-key")
    def test_image_provider_rejects_invalid_base64_and_non_images(self):
        from apps.social.ai import generate_image
        for body in ({"data": []}, {"data": [{"b64_json": "not-base64!"}]}, {"data": [{"b64_json": base64.b64encode(b"not-an-image").decode()}]}, {"data": None}):
            response = Mock(status_code=200)
            response.json.return_value = body
            with self.subTest(body=body), patch("apps.social.ai.requests.post", return_value=response), self.assertRaisesMessage(SocialError, "usable picture"):
                generate_image(subject="A calm reading routine for tonight.")

    @override_settings(SOCIAL_OPENAI_API_KEY="test-key")
    def test_captions_reject_wrong_types_and_preserve_link_line_breaks(self):
        from apps.social.ai import write_captions
        text = "A calm reading routine for tonight.\n\nhttps://clearcodereading.com/"
        for facebook in (None, ["invalid"], "Guaranteed results for your child.", text):
            response = Mock(status_code=200)
            response.json.return_value = {"choices": [{"message": {"content": json.dumps({"facebook": facebook, "instagram": "Try a calm routine. #ClearCodeReading #ReadingAtHome"})}}]}
            with patch("apps.social.ai.requests.post", return_value=response):
                if facebook == text:
                    actual, _ = write_captions(subject="A calm reading routine for tonight.", audience="families", tone="warm", link="https://clearcodereading.com/")
                    self.assertEqual(actual, text)
                else:
                    with self.assertRaises(SocialError):
                        write_captions(subject="A calm reading routine for tonight.", audience="families", tone="warm", link="")
