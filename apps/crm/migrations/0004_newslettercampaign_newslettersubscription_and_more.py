import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0003_lead_audience_linked_user_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="NewsletterCampaign",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("subject", models.CharField(max_length=255)),
                ("preview_text", models.CharField(blank=True, max_length=255)),
                ("body", models.TextField(help_text="Plain text; paragraph breaks are preserved in the HTML email.")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("sending", "Sending"),
                            ("sent", "Sent"),
                            ("partially_failed", "Partially failed"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=24,
                    ),
                ),
                ("sending_started_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("recipient_count", models.PositiveIntegerField(default=0)),
                ("delivered_count", models.PositiveIntegerField(default=0)),
                ("failed_count", models.PositiveIntegerField(default=0)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="newsletter_campaigns_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "sent_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="newsletter_campaigns_sent",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["status", "created_at"], name="crm_newscam_status_created")],
            },
        ),
        migrations.CreateModel(
            name="NewsletterSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("name", models.CharField(blank=True, max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("unsubscribed", "Unsubscribed")],
                        db_index=True,
                        default="active",
                        max_length=16,
                    ),
                ),
                ("consented_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("unsubscribed_at", models.DateTimeField(blank=True, null=True)),
                ("source_path", models.CharField(blank=True, max_length=255)),
                ("consent_version", models.CharField(default="newsletter-v1", max_length=32)),
                ("last_sent_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["status", "created_at"], name="crm_newssub_status_created")],
            },
        ),
        migrations.CreateModel(
            name="NewsletterDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("recipient_email", models.EmailField(max_length=254)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("sent", "Sent"),
                            ("failed", "Failed"),
                            ("skipped", "Skipped after unsubscribe"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                (
                    "campaign",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliveries",
                        to="crm.newslettercampaign",
                    ),
                ),
                (
                    "subscription",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="deliveries",
                        to="crm.newslettersubscription",
                    ),
                ),
            ],
            options={
                "ordering": ["campaign_id", "recipient_email"],
                "verbose_name_plural": "newsletter deliveries",
                "indexes": [models.Index(fields=["campaign", "status"], name="crm_newsdel_campaign_status")],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("campaign", "subscription"),
                        name="unique_newsletter_campaign_subscription",
                    )
                ],
            },
        ),
    ]
