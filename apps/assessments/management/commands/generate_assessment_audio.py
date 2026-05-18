import os

from django.core.management.base import BaseCommand, CommandError

from apps.assessments.audio import (
    ASSESSMENT_AUDIO,
    DEFAULT_MODEL_ID,
    DEFAULT_OUTPUT_FORMAT,
    AudioGenerationError,
    generate_audio_asset,
    get_elevenlabs_api_key,
    normalize_voice_id,
)


class Command(BaseCommand):
    help = "Generate and cache ElevenLabs MP3 audio files for the browser reading assessment."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Regenerate files even when they already exist.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be generated without calling ElevenLabs.")
        parser.add_argument("--no-fail", action="store_true", help="Log ElevenLabs errors but exit successfully.")
        parser.add_argument("--voice-id", default=os.getenv("ELEVENLABS_VOICE_ID"), help="ElevenLabs voice id.")
        parser.add_argument("--model-id", default=os.getenv("ELEVENLABS_MODEL_ID", DEFAULT_MODEL_ID))
        parser.add_argument("--output-format", default=os.getenv("ELEVENLABS_OUTPUT_FORMAT", DEFAULT_OUTPUT_FORMAT))

    def handle(self, *args, **options):
        voice_id = normalize_voice_id(options["voice_id"])
        if not voice_id and not options["dry_run"]:
            raise CommandError("Set ELEVENLABS_VOICE_ID or pass --voice-id.")
        if not get_elevenlabs_api_key() and not options["dry_run"]:
            raise CommandError("Set ELEVENLABS_API_KEY before generating audio.")

        generated = 0
        skipped = 0
        errors = []
        for key, item in ASSESSMENT_AUDIO.items():
            if options["dry_run"]:
                self.stdout.write(f"Would generate DB audio: {key} ({item['filename']})")
                continue

            try:
                asset, did_generate = generate_audio_asset(
                    key,
                    voice_id=voice_id,
                    model_id=options["model_id"],
                    output_format=options["output_format"],
                    force=options["force"],
                )
            except AudioGenerationError as exc:
                errors.append(f"{key}: {exc}")
                self.stderr.write(self.style.WARNING(f"Could not generate {key}: {exc}"))
                if options["no_fail"]:
                    continue
                raise CommandError(str(exc)) from exc
            if did_generate:
                generated += 1
                self.stdout.write(self.style.SUCCESS(f"Generated DB audio: {key} ({asset.byte_length} bytes)"))
            else:
                skipped += 1
                self.stdout.write(f"Skipping existing DB audio: {key}")

        summary = f"Assessment audio complete. Generated {generated}; skipped {skipped}; errors {len(errors)}."
        if errors and not options["no_fail"]:
            raise CommandError("\n".join(errors))
        if errors:
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))
