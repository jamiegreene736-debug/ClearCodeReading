from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, override_settings

from apps.social.exceptions import SocialError
from apps.social.veo import (
    MAX_VIDEO_BYTES,
    VideoRejected,
    _request,
    download_video,
    poll_video,
    start_video,
)


def response(body=None, status=200):
    result = MagicMock(status_code=status)
    result.json.return_value = body or {}
    result.__enter__.return_value = result
    return result


@override_settings(
    SOCIAL_GEMINI_API_KEY="private-key", SOCIAL_VEO_MODEL="veo-3.1-generate-preview"
)
class VeoTests(SimpleTestCase):
    def test_create_uses_documented_google_json_contract(self):
        reply = response({"name": "models/veo-3.1-generate-preview/operations/job_1"})
        with patch("apps.social.veo.requests.request", return_value=reply) as call:
            self.assertEqual(
                start_video("A child carries a book."),
                "models/veo-3.1-generate-preview/operations/job_1",
            )
        self.assertEqual(
            call.call_args.args,
            (
                "POST",
                "https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-generate-preview:predictLongRunning",
            ),
        )
        params = call.call_args.kwargs["json"]["parameters"]
        self.assertEqual(
            params,
            {
                "aspectRatio": "9:16",
                "durationSeconds": 8,
                "resolution": "720p",
                "personGeneration": "allow_all",
                "sampleCount": 1,
            },
        )
        self.assertNotIn("data", call.call_args.kwargs)
        self.assertFalse(call.call_args.kwargs["allow_redirects"])

    def test_malformed_operation_cannot_be_saved_or_polled(self):
        with (
            patch(
                "apps.social.veo._request", return_value=response({"name": "../../bad"})
            ),
            self.assertRaises(SocialError),
        ):
            start_video("A story")
        with (
            patch("apps.social.veo.requests.request") as call,
            self.assertRaises(SocialError),
        ):
            poll_video("https://attacker.example/job")
        call.assert_not_called()

    def test_poll_pending_completed_and_filtered_video(self):
        with patch("apps.social.veo._request", return_value=response({"done": False})):
            self.assertEqual(poll_video("operations/1"), {"status": "in_progress"})
        completed = {
            "done": True,
            "response": {
                "generateVideoResponse": {
                    "generatedSamples": [
                        {
                            "video": {
                                "uri": "https://generativelanguage.googleapis.com/v1beta/files/a:download"
                            }
                        }
                    ]
                }
            },
        }
        with patch("apps.social.veo._request", return_value=response(completed)):
            self.assertEqual(poll_video("operations/1")["status"], "completed")
        for value in [
            {"done": True, "error": {"code": 3}},
            {
                "done": True,
                "response": {"generateVideoResponse": {"raiMediaFilteredCount": 1}},
            },
        ]:
            with patch("apps.social.veo._request", return_value=response(value)):
                self.assertEqual(poll_video("operations/1")["status"], "failed")

    def test_errors_are_actionable_without_exposing_key_or_payload(self):
        for status, phrase in [
            (400, "storyboard"),
            (401, "API key"),
            (403, "billing"),
            (404, "model or job"),
            (429, "quota"),
        ]:
            with (
                self.subTest(status=status),
                patch(
                    "apps.social.veo.requests.request",
                    return_value=response(status=status),
                ),
                self.assertRaisesMessage(VideoRejected, phrase),
            ):
                _request("POST", "models/model:predictLongRunning")
        with (
            patch("apps.social.veo.requests.request", side_effect=requests.Timeout),
            self.assertRaises(SocialError),
        ):
            _request("GET", "operations/1")
        with (
            patch("apps.social.veo._request", return_value=response({})),
            self.assertRaises(SocialError),
        ):
            start_video("A story")

    def test_download_accepts_mp4_and_does_not_forward_key_on_redirect(self):
        first = response(status=302)
        first.headers = {"Location": "https://storage.googleapis.com/videos/a.mp4"}
        second = response()
        second.iter_content.return_value = [b"\x00\x00\x00\x18ftypisom-video"]
        with patch("apps.social.veo.requests.get", side_effect=[first, second]) as call:
            self.assertIn(
                b"ftyp",
                download_video(
                    "https://generativelanguage.googleapis.com/v1beta/files/a:download"
                ),
            )
        self.assertEqual(
            call.call_args_list[0].kwargs["headers"], {"x-goog-api-key": "private-key"}
        )
        self.assertEqual(call.call_args_list[1].kwargs["headers"], {})

    def test_download_blocks_unsafe_urls_and_invalid_files(self):
        for url in [
            "http://127.0.0.1/secret",
            "https://attacker.example/video",
            "https://generativelanguage.googleapis.com@attacker.example/file",
            "https://generativelanguage.googleapis.com:90/file",
        ]:
            with (
                self.subTest(url=url),
                patch("apps.social.veo.requests.get") as call,
                self.assertRaises(SocialError),
            ):
                download_video(url)
            call.assert_not_called()
        for chunk in [b"not an mp4", b"x" * (MAX_VIDEO_BYTES + 1)]:
            reply = response()
            reply.iter_content.return_value = [chunk]
            with (
                patch("apps.social.veo.requests.get", return_value=reply),
                self.assertRaises(SocialError),
            ):
                download_video(
                    "https://generativelanguage.googleapis.com/v1beta/files/a:download"
                )
