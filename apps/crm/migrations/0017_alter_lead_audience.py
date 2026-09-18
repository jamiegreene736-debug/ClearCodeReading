from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0016_calendar_blocks"),
    ]

    operations = [
        migrations.AlterField(
            model_name="lead",
            name="audience",
            field=models.CharField(
                choices=[
                    ("parent", "Parent"),
                    ("teacher", "Teacher"),
                    ("school", "School or District"),
                    ("foundation_donor", "Foundation Donor"),
                    ("foundation_grants", "Foundation Grants"),
                    ("equity_investment", "Equity Investment"),
                    ("teacher_partnership", "Teacher Partnership"),
                    ("other", "Other"),
                ],
                db_index=True,
                default="parent",
                max_length=32,
            ),
        ),
    ]
