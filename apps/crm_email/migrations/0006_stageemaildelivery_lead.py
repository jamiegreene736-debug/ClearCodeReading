# ruff: noqa: RUF012
# Survey introduction emails attach to the contact, not to a deal.

import django.db.models.deletion
from django.db import migrations, models


def backfill_leads(apps, schema_editor):
    StageEmailDelivery = apps.get_model("crm_email", "StageEmailDelivery")
    for delivery in StageEmailDelivery.objects.select_related("deal").iterator():
        if delivery.deal_id and delivery.deal.lead_id:
            delivery.lead_id = delivery.deal.lead_id
            delivery.save(update_fields=["lead"])
        else:
            delivery.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0025_newslettersubscription_lead"),
        ("crm_email", "0005_automatedemailimage_stage_template_key"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stageemaildelivery",
            name="deal",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="crm.opportunity",
            ),
        ),
        migrations.AlterField(
            model_name="stageemaildelivery",
            name="pipeline",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="stageemaildelivery",
            name="lead",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="stage_email_deliveries",
                to="crm.lead",
            ),
        ),
        migrations.RunPython(backfill_leads, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="stageemaildelivery",
            name="lead",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="stage_email_deliveries",
                to="crm.lead",
            ),
        ),
    ]
