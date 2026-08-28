import apps.core.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_recruitinginterest"),
    ]

    operations = [
        migrations.AddField(
            model_name="recruitinginterest",
            name="address",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="recruitinginterest",
            name="cover_letter",
            field=models.FileField(blank=True, upload_to=apps.core.models.recruiting_document_upload_path),
        ),
        migrations.AddField(
            model_name="recruitinginterest",
            name="cover_letter_original_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="recruitinginterest",
            name="how_heard",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="recruitinginterest",
            name="resume",
            field=models.FileField(blank=True, upload_to=apps.core.models.recruiting_document_upload_path),
        ),
        migrations.AddField(
            model_name="recruitinginterest",
            name="resume_original_name",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
