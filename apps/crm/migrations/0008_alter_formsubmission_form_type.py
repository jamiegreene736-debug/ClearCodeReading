from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0007_opportunity_bucket_opportunity_campaign_year_and_more"),
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
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
