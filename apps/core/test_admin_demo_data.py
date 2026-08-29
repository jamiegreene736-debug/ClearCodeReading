from django.contrib import admin
from django.core.management import call_command
from django.test import TestCase

from apps.crm.models import NewsletterCampaign, NewsletterDelivery
from apps.curriculum.models import Lesson
from apps.schools.models import School
from apps.workforce.models import PaymentRun, ProviderOnboarding, SensitiveDataReference


class AdminDemoDataTests(TestCase):
    def setUp(self):
        self.original_auto_create_schema = School.auto_create_schema
        School.auto_create_schema = False

    def tearDown(self):
        School.auto_create_schema = self.original_auto_create_schema

    def test_seed_populates_every_admin_model_without_duplicates(self):
        call_command("seed_admin_demo_data", verbosity=0)
        first_counts = {
            model._meta.label: model._default_manager.count()
            for model in admin.site._registry
        }

        self.assertTrue(all(count > 0 for count in first_counts.values()))
        self.assertTrue(
            Lesson.objects.filter(slug="demo-build-and-read-cvc-words").exists()
        )

        call_command("seed_admin_demo_data", verbosity=0)
        second_counts = {
            model._meta.label: model._default_manager.count()
            for model in admin.site._registry
        }

        self.assertEqual(second_counts, first_counts)

    def test_external_workflows_remain_draft_or_pending(self):
        call_command("seed_admin_demo_data", verbosity=0)

        self.assertEqual(
            NewsletterCampaign.objects.get(
                subject__startswith="Demo newsletter"
            ).status,
            NewsletterCampaign.Status.DRAFT,
        )
        self.assertEqual(
            NewsletterDelivery.objects.get(
                recipient_email="sample.reader@example.com"
            ).status,
            NewsletterDelivery.Status.PENDING,
        )
        self.assertEqual(
            PaymentRun.objects.get(period_start="2026-08-16").status,
            PaymentRun.Status.DRAFT,
        )
        self.assertEqual(
            ProviderOnboarding.objects.get(
                external_onboarding_id="demo-onboarding-reference"
            ).status,
            ProviderOnboarding.Status.NOT_INVITED,
        )
        reference = SensitiveDataReference.objects.get(
            external_subject_id="demo-subject-reference"
        )
        self.assertEqual(reference.status, SensitiveDataReference.Status.PENDING)
        self.assertEqual(
            reference.data_categories, ["tax_form_status", "payment_profile_status"]
        )
