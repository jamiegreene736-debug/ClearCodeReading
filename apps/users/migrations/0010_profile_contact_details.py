from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0009_promote_bethany_brooks_to_super_admin"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="about",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="profile",
            name="city",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="profile",
            name="job_title",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="profile",
            name="organization_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="profile",
            name="postal_code",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="profile",
            name="preferred_contact_method",
            field=models.CharField(
                blank=True,
                choices=[
                    ("email", "Email"),
                    ("phone", "Phone call"),
                    ("text", "Text message"),
                ],
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="region",
            field=models.CharField(blank=True, max_length=80),
        ),
    ]
