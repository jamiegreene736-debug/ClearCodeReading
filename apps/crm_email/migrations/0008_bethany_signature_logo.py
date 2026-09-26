# ruff: noqa: RUF012
from django.db import migrations, models

# The wording installed by 0006; pilots still on it move to the new block.
OLD_SIGNATURE = (
    "Bethany Fleming, M.Ed.\n"
    "Founder & CEO\n"
    "c: (256) 762-8094\n"
    "Website: https://clearcodereading.com\n"
    "Blog: https://clearcodereading.com/blog/"
)
NEW_SIGNATURE = (
    "Bethany Fleming, M.Ed.\n"
    "Founder & CEO\n"
    "bethany@clearcodereading.com\n"
    "Website: https://clearcodereading.com\n"
    "Blog: https://clearcodereading.com/blog/"
)
# Bethany's own mailbox signature is plain formatted text: the compose sanitizer
# does not keep images, so the logo is added by the automated-email renderer only.
NEW_SIGNATURE_HTML = (
    "<p>Bethany Fleming, M.Ed.<br>Founder &amp; CEO<br>"
    '<a href="mailto:bethany@clearcodereading.com">bethany@clearcodereading.com</a><br>'
    'Website: <a href="https://clearcodereading.com">clearcodereading.com</a><br>'
    'Blog: <a href="https://clearcodereading.com/blog/">clearcodereading.com/blog</a></p>'
)


def update_signatures(apps, schema_editor):
    StageEmailPilot = apps.get_model("crm_email", "StageEmailPilot")
    Mailbox = apps.get_model("crm_email", "Mailbox")
    StageEmailPilot.objects.filter(bethany_signature=OLD_SIGNATURE).update(
        bethany_signature=NEW_SIGNATURE
    )
    Mailbox.objects.filter(
        user__first_name__iexact="Bethany", user__last_name__iexact="Fleming"
    ).update(signature=NEW_SIGNATURE_HTML)


class Migration(migrations.Migration):
    dependencies = [
        ("crm_email", "0007_merge_stage_delivery_migrations"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stageemailpilot",
            name="bethany_signature",
            field=models.TextField(default=NEW_SIGNATURE),
        ),
        migrations.RunPython(update_signatures, migrations.RunPython.noop),
    ]
