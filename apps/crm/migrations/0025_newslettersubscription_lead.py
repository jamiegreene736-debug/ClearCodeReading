from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0024_alter_intaketriage_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="newslettersubscription",
            name="lead",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="newsletter_subscriptions",
                to="crm.lead",
            ),
        ),
    ]
