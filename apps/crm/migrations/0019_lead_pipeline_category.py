from django.db import migrations, models

PIPELINE_CATEGORY_CHOICES = [
    ("family_enrollment", "Families / Enrollment"),
    ("referral_partners", "School & Teacher Referral Partners"),
    ("foundation_donors", "Foundation Donors"),
    ("foundation_grants", "Foundation Grants / PRIs"),
    ("equity_investment", "Equity / Investment"),
    ("other", "Other"),
]

LEGACY_AUDIENCE_TO_PIPELINE_CATEGORY = {
    "parent": "family_enrollment",
    "teacher": "referral_partners",
    "school": "referral_partners",
    "teacher_partnership": "referral_partners",
    "foundation_donor": "foundation_donors",
}


def merge_audiences_into_pipeline_categories(apps, schema_editor):
    Lead = apps.get_model("crm", "Lead")
    for legacy, category in LEGACY_AUDIENCE_TO_PIPELINE_CATEGORY.items():
        Lead.objects.filter(audience=legacy).update(audience=category)


def restore_legacy_audiences(apps, schema_editor):
    Lead = apps.get_model("crm", "Lead")
    Lead.objects.filter(audience="family_enrollment").update(audience="parent")
    Lead.objects.filter(audience="referral_partners").update(audience="school")
    Lead.objects.filter(audience="foundation_donors").update(audience="foundation_donor")


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0018_formsubmission_family_resources_form_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="lead",
            name="audience",
            field=models.CharField(
                choices=PIPELINE_CATEGORY_CHOICES,
                db_index=True,
                default="family_enrollment",
                max_length=32,
                verbose_name="pipeline category",
            ),
        ),
        migrations.RunPython(merge_audiences_into_pipeline_categories, restore_legacy_audiences),
    ]
