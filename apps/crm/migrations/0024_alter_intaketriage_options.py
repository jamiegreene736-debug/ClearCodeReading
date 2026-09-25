from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0023_newslettercampaign_body_html"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="intaketriage",
            options={
                "ordering": ["-created_at"],
                "verbose_name": "routing decision",
                "verbose_name_plural": "routing decisions",
            },
        ),
    ]
