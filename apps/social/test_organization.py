from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.social.models import SocialCategory, SocialPost, SocialPublication
from apps.users.models import CustomUser


@override_settings(ENABLE_DEMO_ACCESS=False)
class OrganizationTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.admin = CustomUser.objects.create_user(
            username="organization-admin", role=CustomUser.Role.SUPER_ADMIN
        )

    def setUp(self) -> None:
        self.client.force_login(self.admin)
        self.category = SocialCategory.objects.get(name="Free resources")
        self.post = SocialPost.objects.create(
            facebook_caption="Try this reading resource", category=self.category
        )

    def test_editor_creates_category_and_preserves_it_through_caption_edits(
        self,
    ) -> None:
        response = self.client.post(
            reverse("social:edit", args=[self.post.pk]),
            {
                "mode": "manual",
                "caption": "Updated caption",
                "action": "save",
                "category": "new",
                "category_name": "Activities",
                "category_color": "#123abc",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.category.name, "Activities")
        self.assertEqual(self.post.category.color, "#123ABC")
        self.client.post(
            reverse("social:edit", args=[self.post.pk]),
            {"mode": "manual", "caption": "Again", "action": "save"},
        )
        self.post.refresh_from_db()
        self.assertEqual(self.post.category.name, "Activities")

    def test_invalid_category_rolls_back_caption_and_new_category(self) -> None:
        for name, color in [
            ("free RESOURCES", "#123ABC"),
            ("Bad color", "red;position:fixed"),
        ]:
            response = self.client.post(
                reverse("social:edit", args=[self.post.pk]),
                {
                    "mode": "manual",
                    "caption": "Must not save",
                    "action": "save",
                    "category": "new",
                    "category_name": name,
                    "category_color": color,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.post.refresh_from_db()
            self.assertEqual(self.post.facebook_caption, "Try this reading resource")
        self.assertFalse(SocialCategory.objects.filter(name="Bad color").exists())

    def test_published_metadata_and_shared_color_do_not_change_publication(
        self,
    ) -> None:
        self.post.status = "posted"
        self.post.save()
        publication = SocialPublication.objects.create(
            post=self.post,
            network="facebook",
            status="published",
            external_id="123_456",
            published_at=timezone.now(),
        )
        response = self.client.post(
            reverse("social:post_details", args=[self.post.pk]),
            {
                "action": "category",
                "category": self.category.pk,
                "change_category_color": "on",
                "category_color": "#224466",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        publication.refresh_from_db()
        self.category.refresh_from_db()
        self.assertEqual(self.category.color, "#224466")
        self.assertEqual(self.post.facebook_caption, "Try this reading resource")
        self.assertEqual(self.post.status, "posted")
        self.assertEqual(publication.external_id, "123_456")

    def test_archiving_preserves_history_and_blocks_new_assignment(self) -> None:
        self.client.post(
            reverse("social:categories"),
            {"action": "archive", "category_id": self.category.pk},
        )
        self.category.refresh_from_db()
        self.assertTrue(self.category.archived)
        self.post.refresh_from_db()
        self.assertEqual(self.post.category_id, self.category.pk)
        new = SocialPost.objects.create(facebook_caption="Other")
        self.client.post(
            reverse("social:categories"),
            {"action": "bulk", "posts": [new.pk], "category": self.category.pk},
        )
        new.refresh_from_db()
        self.assertIsNone(new.category_id)
        self.client.post(
            reverse("social:categories"),
            {"action": "restore", "category_id": self.category.pk},
        )
        self.client.post(
            reverse("social:categories"),
            {"action": "bulk", "posts": [new.pk], "category": self.category.pk},
        )
        new.refresh_from_db()
        self.assertEqual(new.category_id, self.category.pk)

    def test_calendar_filters_summary_and_direct_publication(self) -> None:
        now = timezone.now()
        self.post.status, self.post.scheduled_at = "scheduled", now
        self.post.save()
        direct = SocialPost.objects.create(
            facebook_caption="Directly published post",
            status="posted",
            category=self.category,
            post_to_instagram=False,
        )
        SocialPublication.objects.create(
            post=direct,
            network="facebook",
            status="published",
            published_at=now,
            external_id="123",
        )
        response = self.client.get(
            reverse("social:calendar"),
            {"category": self.category.pk, "network": "facebook"},
        )
        self.assertContains(response, "Directly published post")
        self.assertContains(response, "1 posted · 1 scheduled")
        self.assertContains(response, self.category.color)
        response = self.client.get(reverse("social:calendar"), {"status": "scheduled"})
        self.assertNotContains(response, "Directly published post")

    def test_blog_defaults_and_explicit_uncategorization_are_preserved(self) -> None:
        blog = SocialPost.objects.create(source="blog", facebook_caption="Article")
        self.assertEqual(blog.category.system_key, "blog")
        blog.category = None
        blog.save()
        blog.refresh_from_db()
        self.assertIsNone(blog.category)

    def test_other_roles_and_anonymous_cannot_change_metadata(self) -> None:
        user = CustomUser.objects.create_user(
            username="ordinary-organization-user", email="ordinary@example.com"
        )
        self.client.force_login(user)
        for route, args in [
            ("categories", []),
            ("performance", []),
            ("post_details", [self.post.pk]),
        ]:
            self.assertEqual(
                self.client.get(reverse(f"social:{route}", args=args)).status_code, 403
            )
            self.assertEqual(
                self.client.post(
                    reverse(f"social:{route}", args=args),
                    {"action": "category", "category": ""},
                ).status_code,
                403,
            )
        self.client.logout()
        self.assertEqual(self.client.get(reverse("social:categories")).status_code, 302)
