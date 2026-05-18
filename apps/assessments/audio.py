import hashlib
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from apps.assessments.models import AssessmentAudioAsset


DEFAULT_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"

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
    pass


def get_elevenlabs_api_key():
    return os.getenv("ELEVENLABS_API_KEY") or os.getenv("XI_API_KEY")


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
        raise AudioGenerationError(f"ElevenLabs returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise AudioGenerationError(f"Could not connect to ElevenLabs: {exc.reason}") from exc


def generate_audio_asset(key, *, voice_id=None, model_id=None, output_format=None, force=False):
    item = ASSESSMENT_AUDIO.get(key)
    if item is None:
        raise AudioGenerationError(f"Unknown assessment audio key: {key}")

    existing = AssessmentAudioAsset.objects.filter(key=key).first()
    if existing is not None and not force:
        return existing, False

    api_key = get_elevenlabs_api_key()
    voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID")
    model_id = model_id or os.getenv("ELEVENLABS_MODEL_ID", DEFAULT_MODEL_ID)
    output_format = output_format or os.getenv("ELEVENLABS_OUTPUT_FORMAT", DEFAULT_OUTPUT_FORMAT)
    if not voice_id:
        raise AudioGenerationError("Set ELEVENLABS_VOICE_ID before generating audio.")
    if not api_key:
        raise AudioGenerationError("Set ELEVENLABS_API_KEY before generating audio.")

    audio = create_elevenlabs_speech(
        api_key=api_key,
        voice_id=voice_id,
        model_id=model_id,
        output_format=output_format,
        text=item["text"],
    )
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
            "metadata": {"filename": item["filename"]},
        },
    )
    return asset, True
