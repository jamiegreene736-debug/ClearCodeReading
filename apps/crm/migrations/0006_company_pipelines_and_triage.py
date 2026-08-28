from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_legacy_opportunity_stages(apps, schema_editor):
    Opportunity = apps.get_model("crm", "Opportunity")
    stage_map = {
        "discovery": "new",
        "demo": "consultation",
        "proposal": "enrollment_offered",
        "negotiation": "qualified",
        "won": "enrolled",
        "lost": "lost",
    }
    for legacy_stage, pipeline_stage in stage_map.items():
        Opportunity.objects.filter(stage=legacy_stage).update(stage=pipeline_stage)


def restore_legacy_opportunity_stages(apps, schema_editor):
    Opportunity = apps.get_model("crm", "Opportunity")
    stage_map = {
        "new": "discovery",
        "consultation": "demo",
        "enrollment_offered": "proposal",
        "qualified": "negotiation",
        "enrolled": "won",
        "lost": "lost",
    }
    for pipeline_stage, legacy_stage in stage_map.items():
        Opportunity.objects.filter(
            pipeline="family_enrollment",
            stage=pipeline_stage,
        ).update(stage=legacy_stage)


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0005_formsubmission_crmactivity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Company",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("name", models.CharField(db_index=True, max_length=255)),
                ("website", models.URLField(blank=True)),
                ("notes", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="owned_crm_companies", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name_plural": "companies", "ordering": ["name", "id"]},
        ),
        migrations.AddIndex(
            model_name="company",
            index=models.Index(fields=["owner", "name"], name="crm_company_owner_name"),
        ),
        migrations.AddIndex(
            model_name="company",
            index=models.Index(fields=["is_deleted", "name"], name="crm_company_active_name"),
        ),
        migrations.AddField(
            model_name="lead",
            name="company",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="contacts", to="crm.company"),
        ),
        migrations.AddIndex(
            model_name="lead",
            index=models.Index(fields=["company", "status"], name="crm_lead_company_status"),
        ),
        migrations.AddField(
            model_name="opportunity",
            name="company",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="deals", to="crm.company"),
        ),
        migrations.AddField(
            model_name="opportunity",
            name="pipeline",
            field=models.CharField(choices=[("family_enrollment", "Families / Enrollment"), ("referral_partners", "Referral Partners"), ("foundation_donors", "Foundation Donors"), ("foundation_grants", "Foundation Grants / PRIs"), ("equity_investment", "Equity / Investment")], db_index=True, default="family_enrollment", max_length=32),
        ),
        migrations.AddField(
            model_name="opportunity",
            name="related_deals",
            field=models.ManyToManyField(blank=True, to="crm.opportunity"),
        ),
        migrations.RunPython(migrate_legacy_opportunity_stages, restore_legacy_opportunity_stages),
        migrations.AlterField(
            model_name="opportunity",
            name="stage",
            field=models.CharField(choices=[("new", "New inquiry"), ("consultation", "Consultation scheduled"), ("qualified", "Qualified"), ("enrollment_offered", "Enrollment offered"), ("enrolled", "Enrolled"), ("identified", "Identified"), ("contacted", "Contacted"), ("active_partner", "Active partner"), ("inactive", "Inactive"), ("cultivating", "Cultivating"), ("ask_planned", "Ask planned"), ("ask_made", "Ask made"), ("pledged", "Pledged"), ("gift_received", "Gift received"), ("stewardship", "Stewardship"), ("loi", "LOI"), ("application", "Application"), ("submitted", "Submitted"), ("due_diligence", "Due diligence"), ("awarded", "Awarded"), ("reporting_renewal", "Reporting / renewal"), ("terms", "Terms"), ("committed", "Committed"), ("funded", "Funded"), ("lost", "Closed lost"), ("declined", "Declined"), ("passed", "Passed")], db_index=True, default="new", max_length=32),
        ),
        migrations.AlterModelOptions(
            name="opportunity",
            options={"ordering": ["expected_close_date", "-created_at"], "verbose_name": "deal", "verbose_name_plural": "deals"},
        ),
        migrations.AddIndex(
            model_name="opportunity",
            index=models.Index(fields=["pipeline", "stage"], name="crm_deal_pipeline_stage"),
        ),
        migrations.AddIndex(
            model_name="opportunity",
            index=models.Index(fields=["company", "pipeline"], name="crm_deal_company_pipeline"),
        ),
        migrations.CreateModel(
            name="IntakeTriage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source_signal", models.CharField(choices=[("partner_interest", "Family partner interest")], db_index=True, max_length=32)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("resolved", "Resolved"), ("dismissed", "Dismissed")], db_index=True, default="pending", max_length=16)),
                ("selected_pipelines", models.JSONField(blank=True, default=list)),
                ("resolution_notes", models.TextField(blank=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_deals", models.ManyToManyField(blank=True, related_name="source_triage_items", to="crm.opportunity")),
                ("lead", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="triage_items", to="crm.lead")),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="resolved_crm_triage_items", to=settings.AUTH_USER_MODEL)),
                ("submission", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="triage_item", to="crm.formsubmission")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="intaketriage",
            index=models.Index(fields=["status", "created_at"], name="crm_triage_status_created"),
        ),
        migrations.AddIndex(
            model_name="intaketriage",
            index=models.Index(fields=["source_signal", "status"], name="crm_triage_signal_status"),
        ),
    ]
