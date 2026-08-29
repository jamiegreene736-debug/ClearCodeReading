from django.db import migrations


def create_clearcode_payer(apps, schema_editor):
    payer_model = apps.get_model("workforce", "PayerLegalEntity")
    payer_model.objects.get_or_create(
        legal_name="ClearCode",
        defaults={
            "display_name": "ClearCode",
            "jurisdiction_state": "FL",
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("workforce", "0001_initial")]

    operations = [migrations.RunPython(create_clearcode_payer, migrations.RunPython.noop)]
