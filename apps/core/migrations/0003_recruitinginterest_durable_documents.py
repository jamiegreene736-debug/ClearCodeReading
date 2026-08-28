from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_recruitinginterest_application_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="recruitinginterest",
            name="cover_letter_content_type",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="recruitinginterest",
            name="cover_letter_data",
            field=models.BinaryField(blank=True, default=bytes),
        ),
        migrations.AddField(
            model_name="recruitinginterest",
            name="resume_content_type",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="recruitinginterest",
            name="resume_data",
            field=models.BinaryField(blank=True, default=bytes),
        ),
    ]
