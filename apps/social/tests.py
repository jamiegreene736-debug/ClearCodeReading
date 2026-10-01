from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.social.crypto import encrypt_text
from apps.social.drafts import draft_captions
from apps.social.exceptions import SocialError
from apps.social.meta import absolute_image_url, image_signature
from apps.social.models import SocialAccount, SocialPost, SocialPublication
from apps.social.services import EASTERN, publish_due
from apps.users.models import CustomUser

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
        self.assertRedirects(response, f"{reverse('social:edit', kwargs={'pk': post.pk})}?mode=brief")
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
