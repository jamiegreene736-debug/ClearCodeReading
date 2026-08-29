import django.db.models.deletion
import django.utils.timezone
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('intervention_sessions', '0003_sessiontemplate_session_session_template_and_more'),
        ('schools', '0002_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Engagement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('classification', models.CharField(choices=[('pending', 'Pending review'), ('contractor', 'Independent contractor'), ('employee', 'Employee')], db_index=True, default='pending', max_length=16)),
                ('status', models.CharField(choices=[('candidate', 'Candidate'), ('classification_pending', 'Classification pending'), ('onboarding', 'Onboarding'), ('ready', 'Ready to pay'), ('active', 'Active'), ('suspended', 'Suspended'), ('ended', 'Ended')], db_index=True, default='classification_pending', max_length=32)),
                ('work_state', models.CharField(db_index=True, default='FL', max_length=2)),
                ('delivery_context', models.CharField(choices=[('virtual', 'Virtual'), ('clearcode_site', 'ClearCode site'), ('school_site', 'School site'), ('mixed', 'Mixed')], default='virtual', max_length=24)),
                ('starts_on', models.DateField()),
                ('ends_on', models.DateField(blank=True, null=True)),
                ('contract_signed_on', models.DateField(blank=True, null=True)),
                ('first_reportable_payment_on', models.DateField(blank=True, null=True)),
                ('anticipated_calendar_year_compensation', models.DecimalField(blank=True, decimal_places=2, help_text='Operational estimate used only to determine whether Florida reporting is expected.', max_digits=12, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='PayerLegalEntity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('legal_name', models.CharField(max_length=255, unique=True)),
                ('display_name', models.CharField(max_length=255)),
                ('jurisdiction_state', models.CharField(default='FL', max_length=2)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
            ],
            options={
                'verbose_name_plural': 'payer legal entities',
            },
        ),
        migrations.CreateModel(
            name='Credential',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('background_screening', 'Background screening'), ('professional_license', 'Professional license'), ('training', 'Required training')], max_length=32)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('cleared', 'Cleared'), ('requires_action', 'Requires action'), ('expired', 'Expired'), ('not_required', 'Not required')], db_index=True, default='pending', max_length=24)),
                ('expires_on', models.DateField(blank=True, db_index=True, null=True)),
                ('external_reference', models.CharField(blank=True, max_length=255)),
                ('center', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='workforce_credentials', to='schools.school')),
                ('engagement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='credentials', to='workforce.engagement')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='Agreement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('contractor', 'Independent contractor agreement'), ('employment', 'Employment agreement'), ('confidentiality', 'Confidentiality agreement')], max_length=24)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('signed', 'Signed'), ('expired', 'Expired'), ('terminated', 'Terminated')], db_index=True, default='pending', max_length=16)),
                ('effective_on', models.DateField(blank=True, null=True)),
                ('expires_on', models.DateField(blank=True, null=True)),
                ('external_document_id', models.CharField(blank=True, max_length=255)),
                ('engagement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='agreements', to='workforce.engagement')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='PayableItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('service_date', models.DateField(db_index=True)),
                ('description', models.CharField(max_length=255)),
                ('units', models.DecimalField(decimal_places=2, default=Decimal('1.00'), max_digits=8)),
                ('gross_amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('in_run', 'In payment run'), ('paid', 'Paid'), ('void', 'Void')], db_index=True, default='draft', max_length=16)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='workforce_payables_approved', to=settings.AUTH_USER_MODEL)),
                ('center', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='workforce_payables', to='schools.school')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='workforce_payables_created', to=settings.AUTH_USER_MODEL)),
                ('engagement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payables', to='workforce.engagement')),
                ('source_session', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='payable_item', to='intervention_sessions.session')),
            ],
        ),
        migrations.AddField(
            model_name='engagement',
            name='payer',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='engagements', to='workforce.payerlegalentity'),
        ),
        migrations.CreateModel(
            name='PaymentRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('period_start', models.DateField()),
                ('period_end', models.DateField()),
                ('idempotency_key', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('reviewed', 'Reviewed'), ('approved', 'Approved'), ('submitting', 'Submitting'), ('submitted', 'Submitted'), ('settled', 'Settled'), ('failed', 'Failed'), ('canceled', 'Canceled')], db_index=True, default='draft', max_length=16)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('external_batch_id', models.CharField(blank=True, max_length=255)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='payment_runs_approved', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payment_runs_created', to=settings.AUTH_USER_MODEL)),
                ('payer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payment_runs', to='workforce.payerlegalentity')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='payment_runs_reviewed', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Payment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('submitted', 'Submitted'), ('settled', 'Settled'), ('failed', 'Failed'), ('canceled', 'Canceled')], db_index=True, default='queued', max_length=16)),
                ('external_payment_id', models.CharField(blank=True, max_length=255)),
                ('failure_code', models.CharField(blank=True, max_length=120)),
                ('engagement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payments', to='workforce.engagement')),
                ('payable', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='payment', to='workforce.payableitem')),
                ('payment_run', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payments', to='workforce.paymentrun')),
            ],
        ),
        migrations.CreateModel(
            name='ProviderEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('provider', models.CharField(max_length=64)),
                ('external_event_id', models.CharField(max_length=255)),
                ('event_type', models.CharField(max_length=120)),
                ('payload_hash', models.CharField(max_length=64)),
                ('status', models.CharField(choices=[('received', 'Received'), ('processed', 'Processed'), ('rejected', 'Rejected')], db_index=True, default='received', max_length=16)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('error_code', models.CharField(blank=True, max_length=120)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('provider', 'external_event_id'), name='unique_workforce_provider_event')],
            },
        ),
        migrations.CreateModel(
            name='ProviderOnboarding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('provider', models.CharField(max_length=64)),
                ('external_onboarding_id', models.CharField(max_length=255, unique=True)),
                ('status', models.CharField(choices=[('not_invited', 'Not invited'), ('invited', 'Invited'), ('in_progress', 'In progress'), ('requires_action', 'Requires action'), ('ready', 'Ready'), ('disabled', 'Disabled')], db_index=True, default='not_invited', max_length=24)),
                ('invite_expires_at', models.DateTimeField(blank=True, null=True)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('remediation_codes', models.JSONField(blank=True, default=list)),
                ('engagement', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='provider_onboarding', to='workforce.engagement')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='RateSchedule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('unit', models.CharField(choices=[('session', 'Session'), ('hour', 'Hour'), ('fixed', 'Fixed amount')], max_length=16)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('currency', models.CharField(default='USD', max_length=3)),
                ('starts_on', models.DateField()),
                ('ends_on', models.DateField(blank=True, null=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('approved', 'Approved'), ('retired', 'Retired')], db_index=True, default='draft', max_length=16)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='workforce_rates_approved', to=settings.AUTH_USER_MODEL)),
                ('center', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='workforce_rates', to='schools.school')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='workforce_rates_created', to=settings.AUTH_USER_MODEL)),
                ('engagement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='rates', to='workforce.engagement')),
            ],
        ),
        migrations.AddField(
            model_name='payableitem',
            name='rate',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payables', to='workforce.rateschedule'),
        ),
        migrations.CreateModel(
            name='SensitiveDataReference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('custodian', models.CharField(choices=[('external_provider', 'External provider'), ('internal_vault', 'Internal restricted vault')], default='external_provider', max_length=24)),
                ('provider', models.CharField(max_length=64)),
                ('external_subject_id', models.CharField(max_length=255)),
                ('data_categories', models.JSONField(default=list)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('verified', 'Verified'), ('requires_action', 'Requires action'), ('revoked', 'Revoked')], db_index=True, default='pending', max_length=24)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('engagement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sensitive_data_references', to='workforce.engagement')),
            ],
        ),
        migrations.CreateModel(
            name='TaxYearSummary',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tax_year', models.PositiveSmallIntegerField()),
                ('total_paid', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('filing_threshold', models.DecimalField(decimal_places=2, max_digits=12)),
                ('filing_required', models.BooleanField(default=False)),
                ('status', models.CharField(choices=[('tracking', 'Tracking'), ('ready_to_file', 'Ready to file'), ('filed', 'Filed'), ('corrected', 'Corrected'), ('not_required', 'Not required')], db_index=True, default='tracking', max_length=20)),
                ('engagement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='tax_year_summaries', to='workforce.engagement')),
            ],
        ),
        migrations.CreateModel(
            name='WorkerAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('starts_on', models.DateField()),
                ('ends_on', models.DateField(blank=True, null=True)),
                ('center', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='workforce_assignments', to='schools.school')),
                ('engagement', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignments', to='workforce.engagement')),
            ],
        ),
        migrations.CreateModel(
            name='WorkerProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('candidate', 'Candidate'), ('active', 'Active'), ('inactive', 'Inactive')], db_index=True, default='candidate', max_length=16)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='worker_profile', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='engagement',
            name='worker',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='engagements', to='workforce.workerprofile'),
        ),
        migrations.CreateModel(
            name='WorkforceRoleMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('role', models.CharField(choices=[('workforce_admin', 'Workforce administrator'), ('compliance_reviewer', 'Compliance reviewer'), ('finance_preparer', 'Finance preparer'), ('finance_approver', 'Finance approver')], db_index=True, max_length=32)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('payer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='role_memberships', to='workforce.payerlegalentity')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workforce_roles', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='ComplianceTask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('fl_new_hire_report', 'Florida independent-contractor report'), ('federal_1099', 'Federal Form 1099'), ('w9_verification', 'Form W-9 verification'), ('background_screening', 'Background screening review'), ('e_verify', 'E-Verify review')], db_index=True, max_length=32)),
                ('tax_year', models.PositiveSmallIntegerField(blank=True, db_index=True, null=True)),
                ('trigger_date', models.DateField(blank=True, null=True)),
                ('due_date', models.DateField(blank=True, db_index=True, null=True)),
                ('status', models.CharField(choices=[('open', 'Open'), ('scheduled', 'Scheduled'), ('completed', 'Completed'), ('waived', 'Waived'), ('blocked', 'Blocked')], db_index=True, default='open', max_length=16)),
                ('external_reference', models.CharField(blank=True, max_length=255)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('completed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='workforce_compliance_tasks_completed', to=settings.AUTH_USER_MODEL)),
                ('engagement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='compliance_tasks', to='workforce.engagement')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('engagement', 'kind', 'tax_year'), name='unique_engagement_compliance_year')],
            },
        ),
        migrations.CreateModel(
            name='ClassificationReview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('version', models.PositiveIntegerField(editable=False)),
                ('decision', models.CharField(choices=[('contractor', 'Independent contractor'), ('employee', 'Employee'), ('needs_review', 'Needs further review')], db_index=True, max_length=16)),
                ('rationale', models.TextField()),
                ('evidence', models.JSONField(blank=True, default=dict)),
                ('reviewed_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('next_review_due', models.DateField(blank=True, db_index=True, null=True)),
                ('reviewed_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='workforce_classification_reviews', to=settings.AUTH_USER_MODEL)),
                ('engagement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='classification_reviews', to='workforce.engagement')),
            ],
            options={
                'ordering': ['-version'],
                'constraints': [models.UniqueConstraint(fields=('engagement', 'version'), name='unique_classification_review_version')],
            },
        ),
        migrations.AddIndex(
            model_name='paymentrun',
            index=models.Index(fields=['payer', 'status', 'period_end'], name='workforce_p_payer_i_dd43d4_idx'),
        ),
        migrations.AddConstraint(
            model_name='payment',
            constraint=models.CheckConstraint(condition=models.Q(('amount__gt', 0)), name='payment_amount_positive'),
        ),
        migrations.AddIndex(
            model_name='rateschedule',
            index=models.Index(fields=['engagement', 'center', 'status', 'starts_on'], name='workforce_r_engagem_1c9190_idx'),
        ),
        migrations.AddConstraint(
            model_name='rateschedule',
            constraint=models.CheckConstraint(condition=models.Q(('amount__gt', 0)), name='workforce_rate_positive'),
        ),
        migrations.AddIndex(
            model_name='payableitem',
            index=models.Index(fields=['center', 'status', 'service_date'], name='workforce_p_center__2a497e_idx'),
        ),
        migrations.AddConstraint(
            model_name='payableitem',
            constraint=models.CheckConstraint(condition=models.Q(('units__gt', 0)), name='payable_units_positive'),
        ),
        migrations.AddConstraint(
            model_name='payableitem',
            constraint=models.CheckConstraint(condition=models.Q(('gross_amount__gt', 0)), name='payable_gross_positive'),
        ),
        migrations.AddConstraint(
            model_name='sensitivedatareference',
            constraint=models.UniqueConstraint(fields=('provider', 'external_subject_id'), name='unique_sensitive_provider_subject'),
        ),
        migrations.AddConstraint(
            model_name='taxyearsummary',
            constraint=models.UniqueConstraint(fields=('engagement', 'tax_year'), name='unique_engagement_tax_year'),
        ),
        migrations.AddIndex(
            model_name='workerassignment',
            index=models.Index(fields=['center', 'is_active'], name='workforce_w_center__2a2cde_idx'),
        ),
        migrations.AddConstraint(
            model_name='workerassignment',
            constraint=models.UniqueConstraint(fields=('engagement', 'center', 'starts_on'), name='unique_worker_assignment_start'),
        ),
        migrations.AddIndex(
            model_name='engagement',
            index=models.Index(fields=['payer', 'classification', 'status'], name='workforce_e_payer_i_083639_idx'),
        ),
        migrations.AddIndex(
            model_name='engagement',
            index=models.Index(fields=['worker', 'status'], name='workforce_e_worker__460897_idx'),
        ),
        migrations.AddIndex(
            model_name='engagement',
            index=models.Index(fields=['work_state', 'status'], name='workforce_e_work_st_3590c9_idx'),
        ),
        migrations.AddIndex(
            model_name='workforcerolemembership',
            index=models.Index(fields=['user', 'role', 'is_active'], name='workforce_w_user_id_8ef7d5_idx'),
        ),
        migrations.AddConstraint(
            model_name='workforcerolemembership',
            constraint=models.UniqueConstraint(fields=('payer', 'user', 'role'), name='unique_workforce_role'),
        ),
    ]
