from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.assessments.audio import generate_audio_asset, normalize_secret, normalize_voice_id
from apps.assessments.models import Assessment
from apps.assessments.reading_survey import score_reading_survey
from apps.assessments.services import calculate_reading_survey_results
from apps.assessments.views import StatusTransitionSerializer


class AssessmentWorkflowTests(SimpleTestCase):
    def test_status_workflow_choices_exist(self):
        self.assertIn(Assessment.Status.PENDING, Assessment.Status.values)
        self.assertIn(Assessment.Status.HUMAN_REVIEW, Assessment.Status.values)
        self.assertIn(Assessment.Status.COMPLETED, Assessment.Status.values)

    def test_transition_serializer_accepts_human_review(self):
        serializer = StatusTransitionSerializer(data={"status": Assessment.Status.HUMAN_REVIEW})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_transition_serializer_rejects_unknown_status(self):
        serializer = StatusTransitionSerializer(data={"status": "needs_magic"})
        self.assertFalse(serializer.is_valid())

    def test_reading_survey_returns_clear_reading_age(self):
        result = score_reading_survey(
            {
                "phonemicAwareness": 0,
                "letterSound": 0,
                "phonics": 1,
                "advancedPhonics": 1,
                "sightWords": 2,
                "fluency": 1,
                "vocabulary": 0,
                "comprehension": 0,
                "writingReadiness": 1,
                "confidence": 1,
            },
            child_age=7,
        )
        self.assertIn("reading_age", result)
        self.assertIn("Phonics / decoding", result["strengths"])
        self.assertGreater(result["overall_percent"], 60)

    def test_scoring_service_calculates_age_message_and_growth_areas(self):
        responses = [
            self._response("phonics", Decimal("1")),
            self._response("phonics", Decimal("1")),
            self._response("comprehension", Decimal("0")),
            self._response("fluency", Decimal("0.5")),
        ]

        result = calculate_reading_survey_results(responses)

        self.assertEqual(result["category_scores"]["phonics"]["score"], 100)
        self.assertEqual(result["category_scores"]["comprehension"]["score"], 0)
        self.assertIn("Phonics / decoding", result["strengths"])
        self.assertIn("Comprehension", result["growth_areas"])
        self.assertEqual(result["final_message"], f"You are reading at an {result['reading_age']:.1f}-year-old level")
        self.assertGreaterEqual(result["reading_age"], 4.0)
        self.assertLessEqual(result["reading_age"], 11.0)

    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test-key", "ELEVENLABS_VOICE_ID": "test-voice"})
    @patch("apps.assessments.audio.create_elevenlabs_speech", return_value=b"fake-mp3")
    @patch("apps.assessments.audio.AssessmentAudioAsset")
    def test_generate_audio_asset_creates_missing_cached_clip(self, asset_model, create_speech):
        asset_model.objects.filter.return_value.first.return_value = None
        cached_asset = SimpleNamespace(byte_length=8)
        asset_model.objects.update_or_create.return_value = (cached_asset, True)

        asset, did_generate = generate_audio_asset("intro")

        self.assertIs(asset, cached_asset)
        self.assertTrue(did_generate)
        create_speech.assert_called_once()
        asset_model.objects.update_or_create.assert_called_once()
        self.assertEqual(asset_model.objects.update_or_create.call_args.kwargs["key"], "intro")

    @patch("apps.assessments.audio.AssessmentAudioAsset")
    def test_generate_audio_asset_skips_existing_cached_clip(self, asset_model):
        cached_asset = SimpleNamespace(byte_length=8)
        asset_model.objects.filter.return_value.first.return_value = cached_asset

        asset, did_generate = generate_audio_asset("intro")

        self.assertIs(asset, cached_asset)
        self.assertFalse(did_generate)
        asset_model.objects.update_or_create.assert_not_called()

    def test_elevenlabs_secret_normalization_handles_common_copy_paste_values(self):
        self.assertEqual(normalize_secret('"Bearer test-key"'), "test-key")
        self.assertEqual(normalize_secret("ELEVENLABS_API_KEY='test-key'"), "test-key")

    def test_elevenlabs_voice_normalization_extracts_ids_from_urls(self):
        self.assertEqual(
            normalize_voice_id("https://api.elevenlabs.io/v1/text-to-speech/abc123"),
            "abc123",
        )

    def _response(self, category, score_value):
        return SimpleNamespace(
            question=SimpleNamespace(
                category=category,
                question_options=[
                    SimpleNamespace(score_value=Decimal("0")),
                    SimpleNamespace(score_value=Decimal("1")),
                ],
            ),
            selected_option=None,
            selected_option_id=None,
            score_value=score_value,
            is_correct=None,
        )
