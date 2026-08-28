import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_recruitinginterest_durable_documents"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="recruitinginterest",
            name="candidate_pool",
            field=models.CharField(default="ClearCode recruiting", max_length=255),
        ),
        migrations.AddField(
            model_name="recruitinginterest",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="owned_recruiting_interests",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
