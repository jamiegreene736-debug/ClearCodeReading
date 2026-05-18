from django.db import migrations


TEMPLATES = [
    {
        "title": "Beginning Sounds Sprint",
        "slug": "beginning-sounds-sprint",
        "grade_band": "K-1",
        "description": "Short sound-awareness routine for hearing and matching first sounds.",
        "goal": "Hear and match beginning sounds in familiar words.",
        "recommended_minutes": 10,
        "activities": [
            "Say three words aloud and clap when two start the same.",
            "Sort picture cards by beginning sound.",
            "End with one confidence-building reread.",
        ],
        "materials": ["picture cards", "simple word list"],
    },
    {
        "title": "Short Vowel Decoding",
        "slug": "short-vowel-decoding",
        "grade_band": "1-2",
        "description": "Decoding path for CVC words with short vowels.",
        "goal": "Blend sounds smoothly to read short vowel words.",
        "recommended_minutes": 15,
        "activities": [
            "Warm up with letter sounds.",
            "Blend five CVC words aloud.",
            "Read a short decodable sentence.",
        ],
        "materials": ["whiteboard", "CVC word cards", "decodable sentence strip"],
    },
    {
        "title": "Fluency Builder",
        "slug": "fluency-builder",
        "grade_band": "2-3",
        "description": "Repeated reading routine for smoothness, accuracy, and expression.",
        "goal": "Read a familiar passage with smoother pacing and confidence.",
        "recommended_minutes": 12,
        "activities": [
            "Teacher models one sentence.",
            "Child reads the passage twice.",
            "Celebrate one smoother phrase and set one next goal.",
        ],
        "materials": ["short familiar passage", "fluency tracker"],
    },
    {
        "title": "Comprehension Talk",
        "slug": "comprehension-talk",
        "grade_band": "2-4",
        "description": "Simple discussion routine for main idea, evidence, and retell.",
        "goal": "Retell key events and explain thinking with text evidence.",
        "recommended_minutes": 15,
        "activities": [
            "Read a short passage together.",
            "Ask who, what, where, and why questions.",
            "Child gives a one-minute retell with one detail from the text.",
        ],
        "materials": ["short passage", "retell prompts"],
    },
]


def seed_templates(apps, schema_editor):
    LessonTemplate = apps.get_model("curriculum", "LessonTemplate")
    for item in TEMPLATES:
        LessonTemplate.objects.update_or_create(
            slug=item["slug"],
            defaults={**item, "is_active": True, "is_deleted": False, "deleted_at": None},
        )


def unseed_templates(apps, schema_editor):
    LessonTemplate = apps.get_model("curriculum", "LessonTemplate")
    LessonTemplate.objects.filter(slug__in=[item["slug"] for item in TEMPLATES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("curriculum", "0002_lessontemplate_teacherlessontemplate_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_templates, unseed_templates),
    ]
