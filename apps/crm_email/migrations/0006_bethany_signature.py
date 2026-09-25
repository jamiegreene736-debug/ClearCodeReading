# ruff: noqa: RUF012
from django.db import migrations, models

OLD_SIGNATURE = "Bethany Fleming\nFounder & CEO, ClearCode Reading Center\nbethany@clearcodereading.com"
NEW_SIGNATURE = (
    "Bethany Fleming, M.Ed.\n"
    "Founder & CEO\n"
    "c: (256) 762-8094\n"
    "Website: https://clearcodereading.com\n"
    "Blog: https://clearcodereading.com/blog/"
)
NEW_SIGNATURE_HTML = (
    "<p>Bethany Fleming, M.Ed.<br>Founder &amp; CEO<br>c: (256) 762-8094<br>"
    'Website: <a href="https://clearcodereading.com">clearcodereading.com</a><br>'
    'Blog: <a href="https://clearcodereading.com/blog/">clearcodereading.com/blog</a></p>'
)


def update_signatures(apps, schema_editor):
    StageEmailPilot = apps.get_model("crm_email", "StageEmailPilot")
    Mailbox = apps.get_model("crm_email", "Mailbox")
    # Pilots still on the previous default pick up the new wording.
    StageEmailPilot.objects.filter(bethany_signature=OLD_SIGNATURE).update(
        bethany_signature=NEW_SIGNATURE
    )
    # Bethany's own mailbox signature, added to every draft she starts.
    Mailbox.objects.filter(
        user__first_name__iexact="Bethany", user__last_name__iexact="Fleming"
    ).update(signature=NEW_SIGNATURE_HTML)


class Migration(migrations.Migration):
    dependencies = [
        ("crm_email", "0005_automatedemailimage_stage_template_key"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stageemailpilot",
            name="bethany_signature",
            field=models.TextField(default=NEW_SIGNATURE),
        ),
        migrations.RunPython(update_signatures, migrations.RunPython.noop),
    ]
