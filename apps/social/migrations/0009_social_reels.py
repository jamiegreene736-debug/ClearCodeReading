from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("social", "0008_editable_image_prompt")]
    operations = [
        migrations.AddField(
            "socialpost", "is_reel", models.BooleanField(default=False)
        ),
        migrations.AddField(
            "socialpost", "reel_prompt", models.TextField(blank=True, default="")
        ),
        migrations.AddField(
            "socialpost", "reel_job", models.JSONField(blank=True, default=dict)
        ),
        migrations.AddField(
            "socialpost",
            "reel_publications",
            models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            "socialpost", "video_data", models.BinaryField(blank=True, null=True)
        ),
    ]
