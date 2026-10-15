from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.social.ai import write_captions
from apps.social.drafts import draft_captions, share_caption_links
from apps.social.exceptions import SocialError


class CaptionLinkTests(SimpleTestCase):
    def test_copies_exact_urls_once_in_order_and_keeps_caption(self):
        first = "https://example.com/read?utm_source=facebook&topic=phonics#tips"
        second = "https://example.com/a_(book)"
        facebook = f"Read ({first}), then {second}. Again: {first}"
        instagram = "Read together. #ClearCodeReading"
        result = share_caption_links(facebook, instagram)
        self.assertEqual(result, f"{instagram}\n\n{first}\n{second}")
        self.assertEqual(share_caption_links(facebook, result), result)

    def test_existing_link_does_not_hide_a_different_link_with_same_prefix(self):
        result = share_caption_links(
            "https://example.com/read", "Try https://example.com/reading"
        )
        self.assertTrue(result.endswith("\n\nhttps://example.com/read"))

    def test_no_urls_or_invalid_urls_leave_text_unchanged(self):
        for facebook in [
            "Read a book.",
            "javascript:alert(1)",
            "https://",
            "https://invalid",
        ]:
            self.assertEqual(share_caption_links(facebook, "Keep me."), "Keep me.")
        self.assertEqual(
            share_caption_links("Visit www.example.com/read.", "Read."),
            "Read.\n\nwww.example.com/read",
        )

    def test_overflow_is_explicit_and_does_not_truncate_urls(self):
        url = "https://example.com/read"
        caption = "a" * (2200 - len(url) - 2)
        self.assertEqual(len(share_caption_links(url, caption)), 2200)
        with self.assertRaisesMessage(SocialError, "2,200"):
            share_caption_links(url, caption + "a")

    @override_settings(SOCIAL_OPENAI_API_KEY="test")
    @patch("apps.social.ai._write_json")
    def test_ai_output_missing_instagram_link_is_repaired(self, generate):
        facebook = "Try this calm reading routine.\n\nhttps://example.com/reading"
        generate.return_value = {
            "facebook": facebook,
            "instagram": "Read together. #ClearCodeReading",
        }
        actual, instagram = write_captions(
            subject="A calm reading routine for families.",
            audience="families",
            tone="warm",
            link="",
        )
        self.assertEqual(actual, facebook)
        self.assertTrue(instagram.endswith("https://example.com/reading"))

    def test_local_draft_shares_supplied_link(self):
        facebook, instagram = draft_captions(
            brief="Read together for a few minutes.",
            audience="families",
            tone="warm",
            link="https://example.com/reading",
        )
        self.assertTrue(facebook.endswith("https://example.com/reading"))
        self.assertTrue(instagram.endswith("https://example.com/reading"))
