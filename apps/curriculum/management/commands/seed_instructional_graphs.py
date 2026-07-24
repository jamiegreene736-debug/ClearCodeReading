from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_tenants.utils import tenant_context

from apps.curriculum.models import Curriculum, CurriculumSequence
from apps.schools.models import School


PFR_POSITIONS = [
    ("Short a with m, s, t", ["a", "m", "s", "t"], ["VC", "CVC"]),
    ("Short i with f, n, p", ["i", "f", "n", "p"], ["VC", "CVC"]),
    ("Short o with c, h", ["o", "c", "h"], ["CVC"]),
    ("Short e with d, r", ["e", "d", "r"], ["CVC"]),
    ("Short u with b, g", ["u", "b", "g"], ["CVC"]),
    ("Single consonants l, k, j, w", ["l", "k", "j", "w"], ["CVC"]),
    ("Single consonants v, x, y, z, qu", ["v", "x", "y", "z", "qu"], ["CVC"]),
    ("Continuous-sound CVC blending", [], ["CVC continuous-sound"]),
    ("Stop-sound CVC blending", [], ["CVC stop-sound"]),
    ("Digraphs sh and ch", ["sh", "ch"], ["CVC", "CCVC"]),
    ("Digraphs th and wh", ["th", "wh"], ["CVC", "CCVC"]),
    ("Final spelling pattern ck", ["ck"], ["CVC"]),
    ("Final spellings ng and nk", ["ng", "nk"], ["CVCC"]),
    ("Final consonant blends", [], ["CVCC"]),
    ("Initial s-blends", [], ["CCVC"]),
    ("Initial l-blends", [], ["CCVC"]),
    ("Initial r-blends", [], ["CCVC"]),
    ("Inflectional ending s", [], ["CVC+s", "CVCC+s"]),
    ("Inflectional ending ing", [], ["base+ing"]),
    ("Inflectional ending ed", [], ["base+ed"]),
]

PFR_HIGH_FREQUENCY_WORDS = [
    ["the", "a"],
    ["I", "is"],
    ["to", "and"],
    ["you", "of"],
    ["was", "said"],
    ["he", "she"],
    ["we", "they"],
    ["are", "were"],
    ["have", "has"],
    ["do", "does"],
    ["come", "some"],
    ["one", "once"],
    ["two", "who"],
    ["what", "when"],
    ["where", "why"],
    ["there", "here"],
    ["could", "would"],
    ["should", "again"],
    ["because", "before"],
    ["after", "through"],
]


OG_POSITIONS = [
    ("Phoneme awareness: rhyme and first sound", "phonological_awareness", []),
    ("Phoneme awareness: blending", "phonological_awareness", []),
    ("Phoneme awareness: segmenting", "phonological_awareness", []),
    ("Short a in closed syllables", "phonics_concept", ["closed"]),
    ("Consonants c, o, g, a, d", "letter_sound", ["closed"]),
    ("Consonants t, m, l, h", "letter_sound", ["closed"]),
    ("Short i in closed syllables", "phonics_concept", ["closed"]),
    ("Consonants p, n, f, s", "letter_sound", ["closed"]),
    ("Short o in closed syllables", "phonics_concept", ["closed"]),
    ("Consonants r, k, b, j", "letter_sound", ["closed"]),
    ("Short e in closed syllables", "phonics_concept", ["closed"]),
    ("Consonants v, w, x, y, z", "letter_sound", ["closed"]),
    ("Short u in closed syllables", "phonics_concept", ["closed"]),
    ("Digraph sh", "phonics_concept", ["closed"]),
    ("Digraph ch", "phonics_concept", ["closed"]),
    ("Digraph th", "phonics_concept", ["closed"]),
    ("Digraph wh", "phonics_concept", ["closed"]),
    ("Final spelling rule ck", "orthographic_rule", ["closed"]),
    ("FLOSS spelling rule", "orthographic_rule", ["closed"]),
    ("Initial and final blends", "phonics_concept", ["closed"]),
]


class Command(BaseCommand):
    help = "Seed initial center-scoped PFR and OG+ curriculum sequence positions."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--center-schema", help="Schema name of the center to seed.")
        target.add_argument("--all-centers", action="store_true", help="Seed every active center.")

    def handle(self, *args, **options):
        centers = School.objects.filter(is_deleted=False)
        if options["center_schema"]:
            centers = centers.filter(schema_name=options["center_schema"])
            if not centers.exists():
                raise CommandError(f"No active center uses schema {options['center_schema']!r}.")

        count = 0
        for center in centers:
            with tenant_context(center), transaction.atomic():
                pfr = self._upsert_curriculum(
                    center,
                    Curriculum.Code.PFR,
                    "Phonics for Reading",
                )
                og_plus = self._upsert_curriculum(
                    center,
                    Curriculum.Code.OG_PLUS,
                    "IMSE Comprehensive Orton-Gillingham Plus",
                )
                self._seed_pfr(center, pfr)
                self._seed_og_plus(center, og_plus)
                count += 1
                self.stdout.write(self.style.SUCCESS(f"Seeded instructional graphs for {center.name}."))

        if count == 0:
            raise CommandError("No active centers were found.")

    @staticmethod
    def _upsert_curriculum(center, code, name):
        curriculum, _ = Curriculum.objects.update_or_create(
            center=center,
            code=code,
            version="2026.1",
            defaults={
                "name": name,
                "is_active": True,
                "is_deleted": False,
                "deleted_at": None,
                "metadata": {"source": "Phase 0 instructional design lock"},
            },
        )
        return curriculum

    @staticmethod
    def _seed_pfr(center, curriculum):
        previous = None
        for lesson_number, (title, letter_sounds, word_types) in enumerate(PFR_POSITIONS, start=1):
            position, _ = CurriculumSequence.objects.update_or_create(
                curriculum=curriculum,
                code=f"PFR-A-{lesson_number:02d}",
                defaults={
                    "center": center,
                    "sequence_order": lesson_number,
                    "level": CurriculumSequence.PFRLevel.A,
                    "lesson_number": lesson_number,
                    "concept_number": None,
                    "title": title,
                    "position_type": CurriculumSequence.PositionType.PHONICS_CONCEPT,
                    "letter_sounds": letter_sounds,
                    "word_types": word_types,
                    "syllable_types": ["closed"],
                    "high_frequency_words": PFR_HIGH_FREQUENCY_WORDS[lesson_number - 1],
                    "activities": [
                        "phonemic awareness",
                        "sound drill",
                        "word reading",
                        "word spelling",
                        "high-frequency word practice",
                        "connected text",
                    ],
                    "item_set_schema": {
                        "session_1a": ["sound_drill", "word_reading", "word_spelling"],
                        "session_1b": ["review", "high_frequency_words", "connected_text"],
                    },
                    "mastery_criteria": {
                        "word_reading_accuracy_percent": 90,
                        "connected_text_accuracy_percent": 95,
                        "required_consecutive_checks": 2,
                    },
                    "is_deleted": False,
                    "deleted_at": None,
                },
            )
            position.prerequisites.set([previous] if previous else [])
            previous = position

    @staticmethod
    def _seed_og_plus(center, curriculum):
        previous = None
        for concept_number, (title, position_type, syllable_types) in enumerate(OG_POSITIONS, start=1):
            position, _ = CurriculumSequence.objects.update_or_create(
                curriculum=curriculum,
                code=f"OG-{concept_number:03d}",
                defaults={
                    "center": center,
                    "sequence_order": concept_number,
                    "level": "",
                    "lesson_number": None,
                    "concept_number": concept_number,
                    "title": title,
                    "position_type": position_type,
                    "syllable_types": syllable_types,
                    "activities": [
                        "phonological awareness",
                        "three-part drill",
                        "concept instruction",
                        "word reading",
                        "encoding",
                        "connected text",
                    ],
                    "item_set_schema": {
                        "required": ["review_items", "new_concept_items", "encoding_items"],
                        "optional": ["red_words", "connected_text", "dictation"],
                    },
                    "mastery_criteria": {
                        "decoding_accuracy_percent": 90,
                        "encoding_accuracy_percent": 85,
                        "required_consecutive_checks": 2,
                    },
                    "is_deleted": False,
                    "deleted_at": None,
                },
            )
            position.prerequisites.set([previous] if previous else [])
            previous = position
