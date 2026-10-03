"""Exercise navigation with real rendered pages and follow form redirects."""

from datetime import timedelta
from urllib.parse import urlencode

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.social.models import SocialPost
from apps.users.models import CustomUser


@override_settings(ENABLE_DEMO_ACCESS=False, DEBUG=True)
class SocialNavigationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_user(
            username="navigation-admin", role=CustomUser.Role.SUPER_ADMIN
        )
        cls.post = SocialPost.objects.create(
            facebook_caption="A reading idea", created_by=cls.admin
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_every_screen_has_working_list_plan_and_settings_links(self):
        self.post.status = SocialPost.Status.SCHEDULED
        self.post.scheduled_at = timezone.now() + timedelta(days=10)
        self.post.save()
        for route in [
            "new",
            "edit",
            "schedule",
            "cancel",
            "settings",
            "planner",
            "calendar",
            "delete",
        ]:
            args = (
                [self.post.pk]
                if route in {"edit", "schedule", "cancel", "delete"}
                else []
            )
            with self.subTest(route=route):
                response = self.client.get(reverse(f"social:{route}", args=args))
                self.assertEqual(response.status_code, 200)
                for tab in ["drafts", "scheduled", "posted", "attention"]:
                    target = f"{reverse('social:queue')}?tab={tab}"
                    self.assertContains(response, f'href="{target}"')
                    self.assertEqual(self.client.get(target).status_code, 200)
                self.assertContains(response, 'aria-label="Social marketing"')
                self.assertContains(response, 'data-testid="social-back"')

    def test_draft_and_attention_lists_round_trip_through_save(self):
        for tab, status in [("drafts", "draft"), ("attention", "attention")]:
            self.post.status = status
            self.post.save()
            target = reverse("social:edit", args=[self.post.pk]) + f"?return_to={tab}"
            response = self.client.get(reverse("social:queue") + f"?tab={tab}")
            self.assertContains(response, f'href="{target}"')
            response = self.client.post(
                target,
                {"mode": "manual", "caption": "Updated caption", "action": "save"},
                follow=True,
            )
            self.assertContains(
                response,
                f"Back to {'Drafts' if tab == 'drafts' else 'Needs attention'}",
            )
            self.assertIn(f"return_to={tab}", response.redirect_chain[-1][0])
            response = self.client.post(
                target,
                {
                    "mode": "manual",
                    "caption": "Saved and returned",
                    "action": "save_return",
                },
            )
            self.assertRedirects(response, reverse("social:queue") + f"?tab={tab}")
            self.post.refresh_from_db()
            self.assertEqual(self.post.facebook_caption, "Saved and returned")

    def test_planner_origin_survives_modes_save_and_schedule(self):
        edit = reverse("social:edit", args=[self.post.pk]) + "?return_to=planner"
        response = self.client.get(edit)
        self.assertContains(response, "?mode=manual&amp;return_to=planner")
        self.assertContains(response, "Back to AI content plan")
        response = self.client.post(
            edit,
            {"mode": "manual", "caption": "Keep this", "action": "schedule"},
            follow=True,
        )
        self.assertContains(response, f'href="{edit}"')
        self.assertContains(response, "Back to post")
        self.assertContains(response, "?return_to=planner&amp;via=edit&amp;date=")
        response = self.client.post(
            response.redirect_chain[-1][0], {"date": "invalid", "time": "09:00"}
        )
        self.assertContains(response, f'href="{edit}"')
        response = self.client.post(
            edit, {"mode": "manual", "caption": "Keep this", "action": "save_return"}
        )
        self.assertRedirects(response, reverse("social:planner"))

    def test_direct_schedule_returns_to_the_correct_list(self):
        for status, tab in [
            ("draft", "drafts"),
            ("attention", "attention"),
            ("scheduled", "scheduled"),
        ]:
            self.post.status = status
            self.post.save()
            response = self.client.get(reverse("social:schedule", args=[self.post.pk]))
            self.assertEqual(
                response.context["nav"]["back_url"],
                reverse("social:queue") + f"?tab={tab}",
            )

    def test_untrusted_return_targets_fall_back_to_drafts(self):
        for target in ["https://example.com", "//example.com", "/admin/", "unknown"]:
            response = self.client.post(
                reverse("social:new") + "?" + urlencode({"return_to": target}),
                {"mode": "manual", "caption": "Safe return", "action": "save_return"},
            )
            self.assertRedirects(response, reverse("social:queue") + "?tab=drafts")

    def test_new_post_and_settings_preserve_list_origin(self):
        response = self.client.get(reverse("social:queue") + "?tab=attention")
        for route in ["new", "settings", "planner", "calendar"]:
            target = reverse(f"social:{route}") + "?return_to=attention"
            self.assertContains(response, f'href="{target}"')
            page = self.client.get(target)
            self.assertContains(page, "Back to Needs attention")

    def test_planner_navigation_does_not_return_to_itself(self):
        response = self.client.get(reverse("social:planner") + "?return_to=planner")
        self.assertEqual(
            response.context["nav"]["back_url"], reverse("social:queue") + "?tab=drafts"
        )
        self.assertContains(response, reverse("social:settings") + "?return_to=planner")

    def test_calendar_month_survives_edit_schedule_and_delete(self):
        self.post.status = SocialPost.Status.SCHEDULED
        self.post.scheduled_at = timezone.now()
        self.post.save()
        month = timezone.localdate().strftime("%Y-%m")
        origin = f"calendar-{month}"
        calendar = reverse("social:calendar") + f"?month={month}"
        response = self.client.get(calendar)
        self.assertContains(response, f"?return_to={origin}")
        for route in ["edit", "schedule", "delete"]:
            response = self.client.get(
                reverse(f"social:{route}", args=[self.post.pk]) + f"?return_to={origin}"
            )
            self.assertContains(response, f'href="{calendar}"')
            self.assertContains(response, "Back to Calendar")
        response = self.client.post(
            reverse("social:delete", args=[self.post.pk]) + f"?return_to={origin}"
        )
        self.assertRedirects(response, calendar)
        self.assertFalse(SocialPost.objects.filter(pk=self.post.pk).exists())

    def test_delete_from_editor_can_return_without_deleting(self):
        response = self.client.get(
            reverse("social:delete", args=[self.post.pk])
            + "?return_to=planner&via=edit"
        )
        target = reverse("social:edit", args=[self.post.pk]) + "?return_to=planner"
        self.assertContains(response, f'href="{target}"')
        self.assertTrue(SocialPost.objects.filter(pk=self.post.pk).exists())

    def test_invalid_calendar_destinations_use_safe_fallback(self):
        for origin in [
            "calendar-0001-01",
            "calendar-2026-13",
            "calendar-https://example.com",
        ]:
            response = self.client.get(
                reverse("social:edit", args=[self.post.pk]), {"return_to": origin}
            )
            self.assertEqual(
                response.context["nav"]["back_url"],
                reverse("social:queue") + "?tab=drafts",
            )
