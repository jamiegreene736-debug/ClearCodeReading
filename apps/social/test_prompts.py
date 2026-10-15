from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.social.carousel_ai import generate_next_slide
from apps.social.media import Photo
from apps.social.models import SocialPost
from apps.users.models import CustomUser


class EditablePromptTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="prompt-editor", email="prompts@example.com", is_superuser=True
        )
        self.client.force_login(self.user)
        self.post = SocialPost.objects.create(
            created_by=self.user,
            facebook_caption="A reading routine",
            instagram_caption="Read together",
            image_data=b"old-photo",
            image_content_type="image/jpeg",
        )
        self.url = reverse("social:edit", args=[self.post.pk])
        self.prompts = [
            "A family reading together in a bright room.",
            "A close-up of colorful letter tiles on a desk.",
        ]

    def data(self, **changes):
        return {
            "mode": "brief",
            "action": "save_prompts",
            "brief": "A calm family reading routine.",
            "facebook_caption": self.post.facebook_caption,
            "instagram_caption": self.post.instagram_caption,
            "post_to_facebook": "on",
            "post_to_instagram": "on",
            "revision": self.post.updated_at.isoformat(),
            **changes,
        }

    def test_custom_carousel_saves_without_ai_and_keeps_photos(self):
        with (
            patch("apps.social.views.plan_carousel") as plan,
            patch("apps.social.views.generate_image") as image,
        ):
            response = self.client.post(
                self.url, self.data(carousel_prompt=self.prompts, slide_count="2")
            )
        self.assertEqual(response.status_code, 302)
        plan.assert_not_called()
        image.assert_not_called()
        self.post.refresh_from_db()
        self.assertEqual(
            self.post.carousel_plan, {"slides": self.prompts, "completed": 0}
        )
        self.assertEqual(bytes(self.post.image_data), b"old-photo")
        self.assertEqual(self.post.facebook_caption, "A reading routine")
        self.assertContains(self.client.get(self.url), self.prompts[0])

    def test_edit_resets_completed_count_but_unchanged_prompts_keep_progress(self):
        self.post.carousel_plan = {"slides": self.prompts, "completed": 2}
        self.post.save()
        self.client.post(
            self.url, self.data(carousel_prompt=self.prompts, slide_count="2")
        )
        self.post.refresh_from_db()
        self.assertEqual(self.post.carousel_plan["completed"], 2)
        revised = ["A teacher helping a child with letter sounds.", self.prompts[1]]
        self.client.post(self.url, self.data(carousel_prompt=revised, slide_count="2"))
        self.post.refresh_from_db()
        self.assertEqual(self.post.carousel_plan["completed"], 0)
        self.assertEqual(self.post.carousel_plan["slides"], revised)
        self.assertEqual(bytes(self.post.image_data), b"old-photo")

    def test_single_image_uses_and_persists_custom_prompt(self):
        prompt = "A parent and child tracing letters on paper together."
        with patch(
            "apps.social.views.generate_image",
            return_value=(b"new-photo", "image/jpeg"),
        ) as image:
            response = self.client.post(
                self.url, self.data(action="new_image", image_prompt=prompt)
            )
        self.assertEqual(response.status_code, 302)
        image.assert_called_once_with(subject=prompt)
        self.post.refresh_from_db()
        self.assertEqual(self.post.image_prompt, prompt)
        self.assertEqual(bytes(self.post.image_data), b"new-photo")

    def test_invalid_or_stale_edits_leave_saved_plan_unchanged(self):
        self.post.carousel_plan = {"slides": self.prompts, "completed": 1}
        self.post.save()
        for changes in (
            {"carousel_prompt": ["too short", self.prompts[1]], "slide_count": "2"},
            {"carousel_prompt": self.prompts, "slide_count": "3"},
            {"carousel_prompt": self.prompts, "slide_count": "9" * 5000},
            {
                "carousel_prompt": self.prompts,
                "slide_count": "2",
                "revision": "2000-01-01T00:00:00Z",
            },
        ):
            self.client.post(self.url, self.data(**changes))
            self.post.refresh_from_db()
            self.assertEqual(self.post.carousel_plan["completed"], 1)
            self.assertEqual(bytes(self.post.image_data), b"old-photo")

    def test_active_generation_rejects_prompt_change(self):
        self.post.carousel_plan = {
            "slides": self.prompts,
            "completed": 0,
            "token": "in-flight",
        }
        self.post.save()
        response = self.client.post(
            self.url,
            self.data(
                carousel_prompt=self.prompts,
                slide_count="2",
                image_prompt="A new scene of reading together.",
            ),
        )
        self.assertContains(response, "being generated")
        self.post.refresh_from_db()
        self.assertEqual(self.post.image_prompt, "")
        self.assertEqual(self.post.carousel_plan["token"], "in-flight")

    def test_saved_custom_prompts_are_used_for_each_slide(self):
        self.client.post(
            self.url, self.data(carousel_prompt=self.prompts, slide_count="2")
        )
        with (
            patch(
                "apps.social.carousel_ai.generate_image",
                return_value=(b"photo", "image/jpeg"),
            ) as image,
            patch(
                "apps.social.carousel_ai.normalize_photo",
                return_value=Photo(b"photo", "slide.jpg"),
            ),
        ):
            self.assertEqual(generate_next_slide(self.post.pk), (1, 2))
            self.assertEqual(generate_next_slide(self.post.pk), (2, 2))
        self.assertEqual(
            [call.kwargs["subject"] for call in image.call_args_list], self.prompts
        )
        self.post.refresh_from_db()
        self.assertEqual(bytes(self.post.image_data), b"photo")
        self.assertEqual(self.post.slides.count(), 1)

    def test_single_prompt_can_be_saved_without_generating(self):
        prompt = "A family practicing letter sounds at the kitchen table."
        with patch("apps.social.views.generate_image") as image:
            response = self.client.post(self.url, self.data(image_prompt=prompt))
        self.assertEqual(response.status_code, 302)
        image.assert_not_called()
        self.post.refresh_from_db()
        self.assertEqual(self.post.image_prompt, prompt)
        self.assertEqual(bytes(self.post.image_data), b"old-photo")

    def test_integrated_draft_keeps_custom_prompts_while_writing_captions(self):
        with (
            patch("apps.social.views.plan_carousel") as plan,
            patch(
                "apps.social.views.write_captions",
                return_value=("New Facebook caption", "New Instagram caption"),
            ),
        ):
            response = self.client.post(
                self.url,
                self.data(
                    action="draft_ai",
                    generate_carousel="on",
                    carousel_prompt=self.prompts,
                    slide_count="2",
                ),
            )
        self.assertEqual(response.status_code, 302)
        plan.assert_not_called()
        self.post.refresh_from_db()
        self.assertEqual(self.post.carousel_plan["slides"], self.prompts)
        self.assertEqual(self.post.facebook_caption, "New Facebook caption")
        self.assertTrue(self.client.session[f"social_auto_generate_{self.post.pk}"])

    def test_discard_does_not_require_completing_prompt_edits(self):
        self.post.carousel_plan = {"slides": self.prompts, "completed": 2}
        self.post.save()
        response = self.client.post(
            self.url,
            self.data(
                action="discard_carousel",
                carousel_prompt=["unfinished", ""],
                slide_count="2",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.carousel_plan, {})
        self.assertEqual(bytes(self.post.image_data), b"old-photo")
