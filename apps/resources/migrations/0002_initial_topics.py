from typing import ClassVar

from django.db import migrations


def seed_topics(apps, schema_editor):
    Topic = apps.get_model("resources", "Topic")
    for name in [
        "Getting started",
        "Phonics",
        "Fluency",
        "Comprehension",
        "Reading at home",
        "School support",
    ]:
        Topic.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies: ClassVar = [("resources", "0001_initial")]
    operations: ClassVar = [
        migrations.RunPython(seed_topics, migrations.RunPython.noop)
    ]
