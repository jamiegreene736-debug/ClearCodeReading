from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assessments", "0002_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AssessmentAudioAsset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("key", models.SlugField(max_length=80, unique=True)),
                ("text", models.TextField()),
                ("audio", models.BinaryField()),
                ("content_type", models.CharField(default="audio/mpeg", max_length=80)),
                ("provider", models.CharField(db_index=True, default="elevenlabs", max_length=80)),
                ("voice_id", models.CharField(blank=True, max_length=120)),
                ("model_id", models.CharField(blank=True, max_length=120)),
                ("output_format", models.CharField(blank=True, max_length=80)),
                ("byte_length", models.PositiveIntegerField(default=0)),
                ("checksum", models.CharField(blank=True, db_index=True, max_length=64)),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "ordering": ["key"],
                "indexes": [
                    models.Index(fields=["provider", "key"], name="assessments_provide_3a4cca_idx"),
                    models.Index(fields=["checksum"], name="assessments_checksu_bf6221_idx"),
                ],
            },
        ),
    ]
