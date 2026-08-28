from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="RecruitingInterest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("email", models.EmailField(db_index=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=32)),
                ("career_path", models.CharField(choices=[("teacher", "Teaching or reading specialist"), ("company", "Company team")], db_index=True, max_length=16)),
                ("role_interest", models.CharField(max_length=255)),
                ("notes", models.TextField()),
                ("source_path", models.CharField(default="/careers/", max_length=255)),
                ("status", models.CharField(choices=[("new", "New"), ("reviewing", "Reviewing"), ("closed", "Closed")], db_index=True, default="new", max_length=16)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="recruitinginterest",
            index=models.Index(fields=["status", "created_at"], name="core_recruit_status_created"),
        ),
        migrations.AddIndex(
            model_name="recruitinginterest",
            index=models.Index(fields=["career_path", "status"], name="core_recruit_path_status"),
        ),
    ]
