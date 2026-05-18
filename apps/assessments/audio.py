import hashlib
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from apps.assessments.models import AssessmentAudioAsset


DEFAULT_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
DEFAULT_FALLBACK_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

ASSESSMENT_AUDIO = {
    "intro": {
        "filename": "intro.mp3",
        "text": (
            "Hi there. Welcome to Clear Code Reading. First, type your child's name and choose their age. "
            "Then sit together and let your child answer the reading questions."
        ),
    },
    "phonemicAwareness": {
        "filename": "phonemic-awareness.mp3",
        "text": (
            "Sound awareness. Listen for the sss sound: sssun. "
            "Which word starts with that same sss sound? Here are the choices. "
            "Choice 1, sock. Choice 2, moon. Choice 3, ball. Choice 4, table."
        ),
    },
    "letterSound": {
        "filename": "letter-sound.mp3",
        "text": (
            "Letter sound knowledge. Which word starts with the mmm sound, like moon? "
            "Here are the choices. Choice 1, map. Choice 2, sun. Choice 3, top. Choice 4, I am not sure yet."
        ),
    },
    "phonics": {
        "filename": "phonics.mp3",
        "text": (
            "Decoding a simple word. Ask your child to read this word: ship. "
            "How did they do? Choose the answer that fits best."
        ),
    },
    "advancedPhonics": {
        "filename": "advanced-phonics.mp3",
        "text": (
            "Decoding a longer word. Ask your child to read this word: rainbow. "
            "How did they do? Choose the answer that fits best."
        ),
    },
    "sightWords": {
        "filename": "sight-words.mp3",
        "text": (
            "Sight word recognition. How many of these can your child read quickly? "
            "The, said, where, because, friend. Choose the answer that fits best."
        ),
    },
    "fluency": {
        "filename": "fluency.mp3",
        "text": (
            "Reading fluency. Have your child read this sentence: "
            "The little dog ran home because it started to rain. "
            "What best describes the reading?"
        ),
    },
    "vocabulary": {
        "filename": "vocabulary.mp3",
        "text": (
            "Vocabulary. In the sentence, the puppy was exhausted, what does exhausted mean? "
            "Here are the choices. Choice 1, very tired. Choice 2, very hungry. Choice 3, very loud. Choice 4, very small."
        ),
    },
    "comprehension": {
        "filename": "comprehension.mp3",
        "text": (
            "Comprehension. Read this aloud: Mia packed an umbrella before school. Dark clouds filled the sky. "
            "Why did Mia pack an umbrella? Choose the answer that fits best."
        ),
    },
    "writingReadiness": {
        "filename": "writing-readiness.mp3",
        "text": (
            "Spelling and writing readiness. Ask your child to spell the word cat. "
            "What happens? Choose the answer that fits best."
        ),
    },
    "confidence": {
        "filename": "confidence.mp3",
        "text": (
            "Reading confidence. When reading gets tricky, what does your child usually do? "
            "Choose the answer that fits best."
        ),
    },
    "result": {
        "filename": "result.mp3",
        "text": (
            "Your reading snapshot is ready. You will see a reading age estimate, strengths to celebrate, "
            "growth areas, and next steps for a human evaluator to review."
        ),
    },
}


class AudioGenerationError(Exception):
    def __init__(self, message, *, status_code=None, reason="api_error"):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


def get_elevenlabs_api_key():
    return normalize_secret(os.getenv("ELEVENLABS_API_KEY") or os.getenv("XI_API_KEY"))


def normalize_secret(value):
    if not value:
        return ""
    value = value.strip().strip('"').strip("'").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if "=" in value and "\n" not in value:
        value = value.split("=", 1)[1].strip().strip('"').strip("'").strip()
    return value


def normalize_voice_id(value):
    value = normalize_secret(value)
    if not value:
        return ""
    match = re.search(r"/voice(?:s)?/([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    match = re.search(r"/text-to-speech/([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    return value.rstrip("/").split("/")[-1]


def create_elevenlabs_speech(api_key, voice_id, model_id, output_format, text):
    query = urlencode({"output_format": output_format})
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?{query}"
    payload = json.dumps(
        {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.82,
                "style": 0.45,
                "use_speaker_boost": True,
            },
        }
    ).encode("utf-8")
    request = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AudioGenerationError(
            f"ElevenLabs returned HTTP {exc.code}: {detail}",
            status_code=exc.code,
            reason=classify_elevenlabs_error(exc.code, detail),
        ) from exc
    except URLError as exc:
        raise AudioGenerationError(f"Could not connect to ElevenLabs: {exc.reason}", reason="network_error") from exc


def classify_elevenlabs_error(status_code, detail):
    detail = (detail or "").lower()
    if status_code == 429 or "quota" in detail or "rate limit" in detail:
        return "quota_or_rate_limit"
    if status_code == 401:
        return "api_key_rejected"
    if status_code in {400, 403, 404} and "voice" in detail:
        return "voice_id_rejected"
    if status_code == 403:
        return "plan_or_voice_access_rejected"
    return "api_error"


def should_try_fallback_voice(error):
    return error.reason in {"voice_id_rejected", "plan_or_voice_access_rejected"}


def generate_audio_asset(key, *, voice_id=None, model_id=None, output_format=None, force=False):
    item = ASSESSMENT_AUDIO.get(key)
    if item is None:
        raise AudioGenerationError(f"Unknown assessment audio key: {key}")

    existing = AssessmentAudioAsset.objects.filter(key=key).first()
    if existing is not None and not force:
        return existing, False

    api_key = get_elevenlabs_api_key()
    voice_id = normalize_voice_id(voice_id or os.getenv("ELEVENLABS_VOICE_ID"))
    model_id = normalize_secret(model_id or os.getenv("ELEVENLABS_MODEL_ID", DEFAULT_MODEL_ID))
    output_format = normalize_secret(output_format or os.getenv("ELEVENLABS_OUTPUT_FORMAT", DEFAULT_OUTPUT_FORMAT))
    if not voice_id:
        raise AudioGenerationError("Set ELEVENLABS_VOICE_ID before generating audio.")
    if not api_key:
        raise AudioGenerationError("Set ELEVENLABS_API_KEY before generating audio.")

    fallback_for_voice_id = ""
    try:
        audio = create_elevenlabs_speech(
            api_key=api_key,
            voice_id=voice_id,
            model_id=model_id,
            output_format=output_format,
            text=item["text"],
        )
    except AudioGenerationError as exc:
        fallback_voice_id = normalize_voice_id(os.getenv("ELEVENLABS_FALLBACK_VOICE_ID", DEFAULT_FALLBACK_VOICE_ID))
        if not fallback_voice_id or fallback_voice_id == voice_id or not should_try_fallback_voice(exc):
            raise
        fallback_for_voice_id = voice_id
        try:
            audio = create_elevenlabs_speech(
                api_key=api_key,
                voice_id=fallback_voice_id,
                model_id=model_id,
                output_format=output_format,
                text=item["text"],
            )
            voice_id = fallback_voice_id
        except AudioGenerationError:
            raise exc
    checksum = hashlib.sha256(audio).hexdigest()
    asset, _ = AssessmentAudioAsset.objects.update_or_create(
        key=key,
        defaults={
            "text": item["text"],
            "audio": audio,
            "content_type": "audio/mpeg",
            "provider": "elevenlabs",
            "voice_id": voice_id,
            "model_id": model_id,
            "output_format": output_format,
            "byte_length": len(audio),
            "checksum": checksum,
            "metadata": {"filename": item["filename"], "fallback_for_voice_id": fallback_for_voice_id},
        },
    )
    return asset, True
