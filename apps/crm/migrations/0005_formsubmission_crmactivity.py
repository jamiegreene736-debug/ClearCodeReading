import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0004_newslettercampaign_newslettersubscription_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FormSubmission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "form_type",
                    models.CharField(
                        choices=[
                            ("consultation", "Consultation request"),
                            ("assessment", "Assessment follow-up"),
                            ("career", "Career interest"),
                            ("newsletter", "Newsletter signup"),
                            ("website", "Website inquiry"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("source_path", models.CharField(blank=True, db_index=True, max_length=255)),
                ("submitted_data", models.JSONField(default=dict)),
                (
                    "lead",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="form_submissions",
                        to="crm.lead",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="CrmActivity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "activity_type",
                    models.CharField(choices=[("note", "Note"), ("task", "Task")], db_index=True, max_length=16),
                ),
                ("subject", models.CharField(blank=True, max_length=255)),
                ("body", models.TextField(blank=True)),
                ("due_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "assigned_to",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="crm_tasks_assigned",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="crm_activities_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "lead",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="crm_activities",
                        to="crm.lead",
                    ),
                ),
            ],
            options={"verbose_name_plural": "CRM activities", "ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="formsubmission",
            index=models.Index(fields=["form_type", "created_at"], name="crm_form_type_created"),
        ),
        migrations.AddIndex(
            model_name="formsubmission",
            index=models.Index(fields=["lead", "created_at"], name="crm_form_lead_created"),
        ),
        migrations.AddIndex(
            model_name="crmactivity",
            index=models.Index(fields=["lead", "activity_type", "created_at"], name="crm_activity_lead_type"),
        ),
        migrations.AddIndex(
            model_name="crmactivity",
            index=models.Index(fields=["activity_type", "completed_at", "due_at"], name="crm_activity_task_state"),
        ),
    ]
