from django.core.management.base import BaseCommand

from apps.assessments.audio import ASSESSMENT_AUDIO
from apps.assessments.models import AssessmentAudioAsset


class Command(BaseCommand):
    help = "Report which browser assessment audio clips are cached in the database."

    def handle(self, *args, **options):
        cached = {
            asset.key: asset
            for asset in AssessmentAudioAsset.objects.filter(key__in=ASSESSMENT_AUDIO.keys())
        }
        missing = []
        for key in ASSESSMENT_AUDIO:
            asset = cached.get(key)
            if asset is None:
                missing.append(key)
                self.stdout.write(self.style.WARNING(f"Missing: {key}"))
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Cached: {key} ({asset.byte_length} bytes, voice={asset.voice_id or 'unknown'})"
                    )
                )

        if missing:
            self.stdout.write(self.style.WARNING(f"Cached {len(cached)} of {len(ASSESSMENT_AUDIO)} clips."))
        else:
            self.stdout.write(self.style.SUCCESS(f"All {len(ASSESSMENT_AUDIO)} assessment audio clips are cached."))
