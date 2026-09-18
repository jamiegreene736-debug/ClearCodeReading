from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0016_calendar_blocks"),
    ]

    operations = [
        migrations.AlterField(
            model_name="formsubmission",
            name="form_type",
            field=models.CharField(
                choices=[
                    ("consultation", "Consultation request"),
                    ("assessment", "Assessment follow-up"),
                    ("survey", "Early interest survey"),
                    ("career", "Career interest"),
                    ("newsletter", "Newsletter signup"),
                    ("website", "Website inquiry"),
                    ("family_resources", "Free resources modal"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
