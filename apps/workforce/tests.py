from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.curriculum.models import Curriculum, CurriculumSequence
from apps.schools.models import School, SchoolMembership
from apps.sessions.models import Session
from apps.users.models import ChildProfile, CustomUser
from apps.workforce.integrations import StubWorkforceProviderAdapter, WorkforceProviderNotConfigured, get_workforce_provider_adapter
from apps.workforce.models import (
    Agreement,
    ClassificationReview,
    ComplianceTask,
    Engagement,
    PayableItem,
    PaymentRun,
    PayerLegalEntity,
    ProviderOnboarding,
    RateSchedule,
    SensitiveDataReference,
    WorkerProfile,
    WorkforceRoleMembership,
)
from apps.workforce.services import (
    add_payables_to_run,
    approve_payable,
    approve_payment_run,
    approve_rate,
    create_payable_from_session,
    create_provider_invite,
    florida_reporting_deadline,
    payment_readiness,
    record_classification_review,
    record_provider_event,
    review_payment_run,
    submit_payment_run,
)


class WorkforceSecurityContractTests(SimpleTestCase):
    def test_schema_has_no_restricted_value_fields(self):
        restricted_fragments = {
            "ssn",
            "social_security",
            "tax_id",
            "tin",
            "routing",
            "account_number",
            "date_of_birth",
            "passport",
            "drivers_license",
            "w9_document",
        }
        model_names = [
            Engagement,
            ProviderOnboarding,
            SensitiveDataReference,
            PayableItem,
            PaymentRun,
        ]
        field_names = {field.name.lower() for model in model_names for field in model._meta.fields}
        for fragment in restricted_fragments:
            self.assertFalse(any(fragment in field for field in field_names), fragment)

    @override_settings(
        DEBUG=False,
        WORKFORCE_ALLOW_STUB_PROVIDER=True,
        WORKFORCE_PROVIDER_ADAPTER="apps.workforce.integrations.StubWorkforceProviderAdapter",
    )
    def test_stub_provider_fails_closed_outside_debug(self):
        with self.assertRaises(WorkforceProviderNotConfigured):
            get_workforce_provider_adapter()


class WorkforceWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        original = School.auto_create_schema
        School.auto_create_schema = False
        try:
            cls.center = School.objects.create(
                name="Florida Launch Center",
                slug="fl-workforce-launch",
                schema_name="fl_workforce_launch",
            )
            cls.other_center = School.objects.create(
                name="Other Center",
                slug="other-workforce-center",
                schema_name="other_workforce_center",
            )
        finally:
            School.auto_create_schema = original
        cls.payer = PayerLegalEntity.objects.create(
            legal_name="ClearCode Reading LLC",
            display_name="ClearCode",
            jurisdiction_state="FL",
        )
        cls.admin_creator = CustomUser.objects.create_user(
            username="workforce-creator", email="workforce-creator@example.com"
        )
        cls.admin_approver = CustomUser.objects.create_user(
            username="workforce-approver", email="workforce-approver@example.com"
        )
        cls.compliance = CustomUser.objects.create_user(
            username="compliance", email="compliance@example.com"
        )
        cls.finance_creator = CustomUser.objects.create_user(
            username="finance-creator", email="finance-creator@example.com"
        )
        cls.finance_reviewer = CustomUser.objects.create_user(
            username="finance-reviewer", email="finance-reviewer@example.com"
        )
        cls.finance_approver = CustomUser.objects.create_user(
            username="finance-approver", email="finance-approver@example.com"
        )
        cls.center_admin = CustomUser.objects.create_user(
            username="center-admin", email="center-admin@example.com"
        )
        cls.worker_user = CustomUser.objects.create_user(
            username="fl-teacher", email="fl-teacher@example.com", role=CustomUser.Role.TEACHER
        )
        cls.other_worker_user = CustomUser.objects.create_user(
            username="other-teacher", email="other-teacher@example.com", role=CustomUser.Role.TEACHER
        )
        for user in [cls.admin_creator, cls.admin_approver]:
            WorkforceRoleMembership.objects.create(
                payer=cls.payer,
                user=user,
                role=WorkforceRoleMembership.Role.WORKFORCE_ADMIN,
            )
        WorkforceRoleMembership.objects.create(
            payer=cls.payer,
            user=cls.compliance,
            role=WorkforceRoleMembership.Role.COMPLIANCE_REVIEWER,
        )
        for user in [cls.finance_creator, cls.finance_reviewer]:
            WorkforceRoleMembership.objects.create(
                payer=cls.payer,
                user=user,
                role=WorkforceRoleMembership.Role.FINANCE_PREPARER,
            )
        WorkforceRoleMembership.objects.create(
            payer=cls.payer,
            user=cls.finance_approver,
            role=WorkforceRoleMembership.Role.FINANCE_APPROVER,
        )
        SchoolMembership.objects.create(
            school=cls.center,
            user=cls.center_admin,
            role=SchoolMembership.Role.ADMIN,
        )
        cls.worker = WorkerProfile.objects.create(user=cls.worker_user)
        cls.other_worker = WorkerProfile.objects.create(user=cls.other_worker_user)

    def setUp(self):
        self.engagement = Engagement.objects.create(
            payer=self.payer,
            worker=self.worker,
            work_state="FL",
            starts_on=date(2026, 9, 1),
            contract_signed_on=date(2026, 8, 28),
            anticipated_calendar_year_compensation=Decimal("5000.00"),
        )
        self.engagement.assignments.create(center=self.center, starts_on=date(2026, 9, 1))

    def _classify_contractor(self):
        return record_classification_review(
            engagement=self.engagement,
            decision=ClassificationReview.Decision.CONTRACTOR,
            rationale="ClearCode counsel reviewed the control and independence factors.",
            evidence={"control_review": "completed", "review_ticket": "LEGAL-42"},
            reviewer=self.compliance,
            next_review_due=date(2027, 8, 28),
        )

    def _make_ready(self):
        self._classify_contractor()
        ProviderOnboarding.objects.create(
            engagement=self.engagement,
            provider="stub",
            external_onboarding_id=f"ready-{self.engagement.pk}",
            status=ProviderOnboarding.Status.READY,
        )
        Agreement.objects.create(
            engagement=self.engagement,
            kind=Agreement.Kind.CONTRACTOR,
            status=Agreement.Status.SIGNED,
            effective_on=date(2026, 8, 28),
        )
        self.engagement.refresh_from_db()

    def test_teacher_role_does_not_classify_worker(self):
        self.assertEqual(self.worker_user.role, CustomUser.Role.TEACHER)
        self.assertEqual(self.engagement.classification, Engagement.Classification.PENDING)
        self.assertEqual(self.engagement.classification_reviews.count(), 0)

    def test_classification_is_versioned_and_creates_florida_deadline(self):
        review = self._classify_contractor()
        self.engagement.refresh_from_db()

        self.assertEqual(review.version, 1)
        self.assertEqual(self.engagement.classification, Engagement.Classification.CONTRACTOR)
        self.assertEqual(florida_reporting_deadline(self.engagement), date(2026, 9, 17))
        task = ComplianceTask.objects.get(
            engagement=self.engagement,
            kind=ComplianceTask.Kind.FL_NEW_HIRE_REPORT,
            tax_year=2026,
        )
        self.assertEqual(task.trigger_date, date(2026, 8, 28))
        self.assertEqual(task.due_date, date(2026, 9, 17))

    def test_classification_rejects_restricted_data(self):
        with self.assertRaisesMessage(ValidationError, "Restricted data key"):
            record_classification_review(
                engagement=self.engagement,
                decision=ClassificationReview.Decision.CONTRACTOR,
                rationale="Review completed.",
                evidence={"identity": {"ssn": "000-00-0000"}},
                reviewer=self.compliance,
            )
        self.assertFalse(ClassificationReview.objects.filter(engagement=self.engagement).exists())
        with self.assertRaisesMessage(ValidationError, "resembles a restricted"):
            record_classification_review(
                engagement=self.engagement,
                decision=ClassificationReview.Decision.CONTRACTOR,
                rationale="Review completed.",
                evidence={"review_notes": "Provider received 000-00-0000"},
                reviewer=self.compliance,
            )

    @override_settings(DEBUG=True, WORKFORCE_ALLOW_STUB_PROVIDER=True)
    def test_provider_invite_returns_url_without_persisting_it(self):
        self._classify_contractor()
        onboarding, invite_url = create_provider_invite(
            engagement=self.engagement,
            actor=self.admin_creator,
            adapter=StubWorkforceProviderAdapter(),
        )
        self.assertTrue(invite_url.startswith("https://stub.invalid/"))
        field_names = {field.name for field in ProviderOnboarding._meta.fields}
        self.assertNotIn("invite_url", field_names)
        self.assertNotIn("invite_token", field_names)
        reference = SensitiveDataReference.objects.get(engagement=self.engagement)
        self.assertEqual(reference.custodian, SensitiveDataReference.Custodian.EXTERNAL_PROVIDER)
        self.assertEqual(reference.data_categories, ["tax_identity", "payment_account"])
        self.assertEqual(onboarding.status, ProviderOnboarding.Status.INVITED)

    def test_payment_readiness_lists_operational_blockers(self):
        initial = payment_readiness(self.engagement)
        self.assertFalse(initial.ready)
        self.assertIn("classification_pending", initial.blockers)
        self._make_ready()
        ready = payment_readiness(self.engagement)
        self.assertTrue(ready.ready, ready.blockers)

    def test_rate_and_payable_require_different_approvers(self):
        self._make_ready()
        rate = RateSchedule.objects.create(
            engagement=self.engagement,
            center=self.center,
            unit=RateSchedule.Unit.SESSION,
            amount=Decimal("75.00"),
            starts_on=date(2026, 9, 1),
            created_by=self.admin_creator,
        )
        with self.assertRaisesMessage(ValidationError, "different people"):
            approve_rate(rate=rate, actor=self.admin_creator)
        rate.refresh_from_db()
        self.assertEqual(rate.status, RateSchedule.Status.DRAFT)
        approve_rate(rate=rate, actor=self.admin_approver)
        payable = PayableItem.objects.create(
            engagement=self.engagement,
            center=self.center,
            service_date=date(2026, 9, 2),
            description="Completed reading session",
            units=Decimal("1.00"),
            rate=rate,
            gross_amount=Decimal("75.00"),
            status=PayableItem.Status.SUBMITTED,
            created_by=self.worker_user,
        )
        with self.assertRaises(PermissionDenied):
            approve_payable(payable=payable, actor=self.worker_user)
        approve_payable(payable=payable, actor=self.center_admin)
        payable.refresh_from_db()
        self.assertEqual(payable.status, PayableItem.Status.APPROVED)

        self_approved = PayableItem.objects.create(
            engagement=self.engagement,
            center=self.center,
            service_date=date(2026, 9, 3),
            description="Center-entered adjustment",
            units=Decimal("1.00"),
            rate=rate,
            gross_amount=Decimal("75.00"),
            status=PayableItem.Status.SUBMITTED,
            created_by=self.center_admin,
        )
        with self.assertRaisesMessage(ValidationError, "different people"):
            approve_payable(payable=self_approved, actor=self.center_admin)

    def test_completed_instructional_session_creates_one_payable(self):
        self._make_ready()
        rate = RateSchedule.objects.create(
            engagement=self.engagement,
            center=self.center,
            unit=RateSchedule.Unit.SESSION,
            amount=Decimal("75.00"),
            starts_on=date(2026, 9, 1),
            status=RateSchedule.Status.APPROVED,
            created_by=self.admin_creator,
            approved_by=self.admin_approver,
            approved_at=timezone.now(),
        )
        curriculum = Curriculum.objects.create(center=self.center, code=Curriculum.Code.PFR, name="PFR")
        position = CurriculumSequence.objects.create(
            center=self.center,
            curriculum=curriculum,
            code="PFR-WORKFORCE-001",
            sequence_order=1,
            level="A",
            lesson_number=1,
            title="Short vowels",
            position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
        )
        child = ChildProfile.objects.create(first_name="Reader", school=self.center)
        starts_at = timezone.make_aware(datetime(2026, 9, 2, 14, 0))
        session = Session.objects.create(
            center=self.center,
            child=child,
            specialist=self.worker_user,
            curriculum_position=position,
            status=Session.Status.COMPLETED,
            intervention_part=Session.InterventionPart.PFR_1A,
            scheduled_start=starts_at,
            started_at=starts_at,
            ended_at=starts_at + timedelta(minutes=50),
        )

        first = create_payable_from_session(session=session, actor=self.worker_user)
        second = create_payable_from_session(session=session, actor=self.worker_user)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.rate, rate)
        self.assertEqual(first.gross_amount, Decimal("75.00"))
        self.assertEqual(first.status, PayableItem.Status.SUBMITTED)

    def test_payment_run_requires_three_people_and_submission_is_idempotent(self):
        self._make_ready()
        rate = RateSchedule.objects.create(
            engagement=self.engagement,
            center=self.center,
            unit=RateSchedule.Unit.SESSION,
            amount=Decimal("75.00"),
            starts_on=date(2026, 9, 1),
            status=RateSchedule.Status.APPROVED,
            created_by=self.admin_creator,
            approved_by=self.admin_approver,
            approved_at=timezone.now(),
        )
        payable = PayableItem.objects.create(
            engagement=self.engagement,
            center=self.center,
            service_date=date(2026, 9, 2),
            description="Completed reading session",
            units=Decimal("1.00"),
            rate=rate,
            gross_amount=Decimal("75.00"),
            status=PayableItem.Status.APPROVED,
            created_by=self.worker_user,
            approved_by=self.center_admin,
            approved_at=timezone.now(),
        )
        payment_run = PaymentRun.objects.create(
            payer=self.payer,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 15),
            created_by=self.finance_creator,
        )
        add_payables_to_run(payment_run=payment_run, payables=[payable], actor=self.finance_creator)
        with self.assertRaisesMessage(ValidationError, "different people"):
            review_payment_run(payment_run=payment_run, actor=self.finance_creator)
        review_payment_run(payment_run=payment_run, actor=self.finance_reviewer)
        with self.assertRaises(PermissionDenied):
            approve_payment_run(payment_run=payment_run, actor=self.finance_reviewer)
        approve_payment_run(payment_run=payment_run, actor=self.finance_approver)
        first = submit_payment_run(
            payment_run=payment_run,
            actor=self.finance_creator,
            adapter=StubWorkforceProviderAdapter(),
        )
        second = submit_payment_run(
            payment_run=payment_run,
            actor=self.finance_creator,
            adapter=StubWorkforceProviderAdapter(),
        )
        self.assertEqual(first.external_batch_id, second.external_batch_id)
        self.assertEqual(payment_run.payments.count(), 1)
        payment = payment_run.payments.get()
        event, created = record_provider_event(
            provider="stub",
            body=f"evt-settled:payment.updated:{payment.external_payment_id}:settled".encode(),
            signature="stub-valid-signature",
            adapter=StubWorkforceProviderAdapter(),
        )
        payment.refresh_from_db()
        payable.refresh_from_db()
        payment_run.refresh_from_db()
        self.assertTrue(created)
        self.assertEqual(event.status, event.Status.PROCESSED)
        self.assertEqual(payment.status, payment.Status.SETTLED)
        self.assertEqual(payable.status, PayableItem.Status.PAID)
        self.assertEqual(payment_run.status, PaymentRun.Status.SETTLED)
        self.assertEqual(self.engagement.tax_year_summaries.get(tax_year=2026).total_paid, Decimal("75.00"))

    def test_provider_events_are_hashed_and_deduplicated(self):
        body = b"evt-1:payment.updated:payment-1:settled"
        first, created = record_provider_event(
            provider="stub",
            body=body,
            signature="stub-valid-signature",
            adapter=StubWorkforceProviderAdapter(),
        )
        second, created_again = record_provider_event(
            provider="stub",
            body=body,
            signature="stub-valid-signature",
            adapter=StubWorkforceProviderAdapter(),
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(len(first.payload_hash), 64)
        self.assertFalse(hasattr(first, "payload"))

    def test_worker_api_cannot_see_another_worker(self):
        other_engagement = Engagement.objects.create(
            payer=self.payer,
            worker=self.other_worker,
            starts_on=date(2026, 9, 1),
        )
        client = APIClient()
        client.force_authenticate(self.worker_user)
        response = client.get("/api/v1/workforce/engagements/")
        self.assertEqual(response.status_code, 200, response.data)
        ids = {item["id"] for item in response.data["results"]}
        self.assertIn(self.engagement.pk, ids)
        self.assertNotIn(other_engagement.pk, ids)

    def test_center_admin_cannot_view_rates(self):
        client = APIClient()
        client.force_authenticate(self.center_admin)
        response = client.get("/api/v1/workforce/rates/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["results"], [])
        engagement_response = client.get(f"/api/v1/workforce/engagements/{self.engagement.pk}/")
        self.assertEqual(engagement_response.status_code, 200, engagement_response.data)
        self.assertNotIn("anticipated_calendar_year_compensation", engagement_response.data)
