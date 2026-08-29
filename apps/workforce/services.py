from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.schools.models import SchoolMembership
from apps.sessions.models import Session
from apps.users.models import AuditLog
from apps.workforce.access import has_workforce_role, is_global_admin
from apps.workforce.integrations import WorkforceProviderAdapter, get_workforce_provider_adapter
from apps.workforce.models import (
    Agreement,
    ClassificationReview,
    ComplianceTask,
    Credential,
    Engagement,
    PayableItem,
    Payment,
    PaymentRun,
    ProviderEvent,
    ProviderOnboarding,
    RateSchedule,
    SensitiveDataReference,
    TaxYearSummary,
    WorkforceRoleMembership,
)


PROHIBITED_EVIDENCE_KEYS = {
    "ssn",
    "social_security_number",
    "tin",
    "tax_id",
    "ein",
    "routing_number",
    "account_number",
    "bank_account",
    "date_of_birth",
    "dob",
    "w9",
    "identity_document",
    "drivers_license",
    "passport",
}
RESTRICTED_VALUE_PATTERNS = (
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b\d{9}\b"),
)


def _assert_safe_mapping(value, *, path: str = "evidence") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
            if normalized in PROHIBITED_EVIDENCE_KEYS:
                raise ValidationError({path: f"Restricted data key '{key}' must be stored by the provider, not ClearCode."})
            _assert_safe_mapping(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_safe_mapping(nested, path=f"{path}.{index}")
    elif isinstance(value, str) and any(pattern.search(value) for pattern in RESTRICTED_VALUE_PATTERNS):
        raise ValidationError({path: "This value resembles a restricted tax or identity number; store it with the provider."})


def _audit(*, actor, action: str, instance, after: dict | None = None) -> None:
    AuditLog.objects.create(
        actor=actor,
        action=action,
        entity_type=f"workforce.{instance._meta.model_name}",
        entity_id=str(instance.pk),
        after=after or {},
    )


def _require_role(actor, payer, *roles: str) -> None:
    if not has_workforce_role(actor, payer, *roles):
        raise PermissionDenied("The required ClearCode workforce role is missing.")


def florida_reporting_deadline(engagement: Engagement):
    threshold = Decimal(str(getattr(settings, "WORKFORCE_FLORIDA_REPORTING_THRESHOLD", "600.00")))
    expected_to_qualify = (
        engagement.anticipated_calendar_year_compensation is not None
        and engagement.anticipated_calendar_year_compensation >= threshold
    )
    triggers = [engagement.first_reportable_payment_on]
    if expected_to_qualify:
        triggers.append(engagement.contract_signed_on)
    triggers = [value for value in triggers if value is not None]
    if engagement.work_state != "FL" or engagement.classification != Engagement.Classification.CONTRACTOR or not triggers:
        return None
    return min(triggers) + timedelta(days=20)


def ensure_florida_reporting_task(engagement: Engagement) -> ComplianceTask | None:
    deadline = florida_reporting_deadline(engagement)
    if deadline is None:
        return None
    trigger_date = deadline - timedelta(days=20)
    task, _ = ComplianceTask.objects.update_or_create(
        engagement=engagement,
        kind=ComplianceTask.Kind.FL_NEW_HIRE_REPORT,
        tax_year=trigger_date.year,
        defaults={"trigger_date": trigger_date, "due_date": deadline},
    )
    return task


@transaction.atomic
def record_classification_review(
    *,
    engagement: Engagement,
    decision: str,
    rationale: str,
    evidence: dict,
    reviewer,
    next_review_due=None,
) -> ClassificationReview:
    _require_role(reviewer, engagement.payer, WorkforceRoleMembership.Role.COMPLIANCE_REVIEWER)
    if not rationale.strip():
        raise ValidationError({"rationale": "A classification rationale is required."})
    _assert_safe_mapping(evidence)
    _assert_safe_mapping(rationale, path="rationale")
    locked = Engagement.objects.select_for_update().get(pk=engagement.pk)
    version = (locked.classification_reviews.aggregate(latest=Max("version"))["latest"] or 0) + 1
    review = ClassificationReview.objects.create(
        engagement=locked,
        version=version,
        decision=decision,
        rationale=rationale.strip(),
        evidence=evidence,
        reviewed_by=reviewer,
        next_review_due=next_review_due,
    )
    classification = {
        ClassificationReview.Decision.CONTRACTOR: Engagement.Classification.CONTRACTOR,
        ClassificationReview.Decision.EMPLOYEE: Engagement.Classification.EMPLOYEE,
        ClassificationReview.Decision.NEEDS_REVIEW: Engagement.Classification.PENDING,
    }[decision]
    locked.classification = classification
    locked.status = (
        Engagement.Status.ONBOARDING
        if classification != Engagement.Classification.PENDING
        else Engagement.Status.CLASSIFICATION_PENDING
    )
    locked.save(update_fields=["classification", "status", "updated_at"])
    ensure_florida_reporting_task(locked)
    _audit(
        actor=reviewer,
        action="workforce.classification_reviewed",
        instance=locked,
        after={"decision": decision, "version": version, "next_review_due": str(next_review_due or "")},
    )
    return review


@transaction.atomic
def create_provider_invite(
    *, engagement: Engagement, actor, adapter: WorkforceProviderAdapter | None = None
):
    _require_role(actor, engagement.payer, WorkforceRoleMembership.Role.WORKFORCE_ADMIN)
    locked = Engagement.objects.select_for_update().get(pk=engagement.pk)
    if locked.classification == Engagement.Classification.PENDING:
        raise ValidationError("A human classification decision is required before provider onboarding.")
    adapter = adapter or get_workforce_provider_adapter()
    invite = adapter.create_onboarding_invite(engagement=locked)
    onboarding, _ = ProviderOnboarding.objects.update_or_create(
        engagement=locked,
        defaults={
            "provider": adapter.provider,
            "external_onboarding_id": invite.external_onboarding_id,
            "status": ProviderOnboarding.Status.INVITED,
            "invite_expires_at": invite.expires_at,
            "last_synced_at": timezone.now(),
            "remediation_codes": [],
        },
    )
    SensitiveDataReference.objects.update_or_create(
        engagement=locked,
        provider=adapter.provider,
        external_subject_id=invite.external_onboarding_id,
        defaults={
            "custodian": SensitiveDataReference.Custodian.EXTERNAL_PROVIDER,
            "data_categories": ["tax_identity", "payment_account"],
            "status": SensitiveDataReference.Status.PENDING,
        },
    )
    locked.status = Engagement.Status.ONBOARDING
    locked.save(update_fields=["status", "updated_at"])
    _audit(
        actor=actor,
        action="workforce.provider_invite_created",
        instance=onboarding,
        after={"provider": adapter.provider, "status": onboarding.status, "expires_at": invite.expires_at.isoformat()},
    )
    return onboarding, invite.url


@transaction.atomic
def sync_onboarding(*, onboarding: ProviderOnboarding, actor, adapter: WorkforceProviderAdapter | None = None):
    _require_role(actor, onboarding.engagement.payer, WorkforceRoleMembership.Role.WORKFORCE_ADMIN)
    locked = ProviderOnboarding.objects.select_for_update().select_related("engagement").get(pk=onboarding.pk)
    adapter = adapter or get_workforce_provider_adapter()
    if adapter.provider != locked.provider:
        raise ValidationError("Configured provider does not match this onboarding record.")
    state = adapter.get_onboarding_state(external_onboarding_id=locked.external_onboarding_id)
    allowed = set(ProviderOnboarding.Status.values)
    if state.status not in allowed:
        raise ValidationError("Provider returned an unsupported onboarding status.")
    locked.status = state.status
    locked.remediation_codes = list(state.remediation_codes)
    locked.last_synced_at = timezone.now()
    locked.save(update_fields=["status", "remediation_codes", "last_synced_at", "updated_at"])
    _audit(
        actor=actor,
        action="workforce.onboarding_synced",
        instance=locked,
        after={"provider": locked.provider, "status": locked.status, "remediation_codes": locked.remediation_codes},
    )
    return locked


@dataclass(frozen=True)
class PaymentReadiness:
    ready: bool
    blockers: tuple[str, ...]


def payment_readiness(engagement: Engagement, *, today=None) -> PaymentReadiness:
    today = today or timezone.localdate()
    blockers: list[str] = []
    if engagement.classification == Engagement.Classification.PENDING:
        blockers.append("classification_pending")
    latest_review = engagement.classification_reviews.order_by("-version").first()
    if latest_review is None or latest_review.decision == ClassificationReview.Decision.NEEDS_REVIEW:
        blockers.append("classification_review_missing")
    elif latest_review.next_review_due and latest_review.next_review_due < today:
        blockers.append("classification_review_expired")
    try:
        onboarding = engagement.provider_onboarding
    except ProviderOnboarding.DoesNotExist:
        blockers.append("provider_onboarding_missing")
    else:
        if onboarding.status != ProviderOnboarding.Status.READY:
            blockers.append("provider_onboarding_incomplete")
    expected_agreement = (
        Agreement.Kind.CONTRACTOR
        if engagement.classification == Engagement.Classification.CONTRACTOR
        else Agreement.Kind.EMPLOYMENT
    )
    if not engagement.agreements.filter(
        kind=expected_agreement,
        status=Agreement.Status.SIGNED,
        effective_on__lte=today,
    ).filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today)).exists():
        blockers.append("agreement_missing")
    if engagement.delivery_context != Engagement.DeliveryContext.VIRTUAL:
        screening = engagement.credentials.filter(kind=Credential.Kind.BACKGROUND_SCREENING).order_by("-created_at").first()
        if screening is None or screening.status not in [Credential.Status.CLEARED, Credential.Status.NOT_REQUIRED]:
            blockers.append("background_screening_incomplete")
        elif screening.expires_on and screening.expires_on < today:
            blockers.append("background_screening_expired")
    florida_deadline = florida_reporting_deadline(engagement)
    if florida_deadline:
        florida_task = engagement.compliance_tasks.filter(
            kind=ComplianceTask.Kind.FL_NEW_HIRE_REPORT,
            tax_year=(florida_deadline - timedelta(days=20)).year,
        ).first()
        if florida_task is None:
            blockers.append("florida_reporting_task_missing")
        elif florida_task.due_date and florida_task.due_date < today and florida_task.status not in [
            ComplianceTask.Status.COMPLETED,
            ComplianceTask.Status.WAIVED,
        ]:
            blockers.append("florida_reporting_overdue")
    return PaymentReadiness(ready=not blockers, blockers=tuple(blockers))


def _effective_rate(*, engagement: Engagement, center, service_date):
    return (
        engagement.rates.filter(status=RateSchedule.Status.APPROVED, starts_on__lte=service_date)
        .filter(Q(center=center) | Q(center__isnull=True))
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=service_date))
        .order_by("-center_id", "-starts_on", "-created_at")
        .first()
    )


@transaction.atomic
def create_payable_from_session(*, session: Session, actor) -> PayableItem:
    locked_session = Session.objects.select_for_update().get(pk=session.pk)
    if locked_session.status != Session.Status.COMPLETED:
        raise ValidationError("Only a completed session can create a payable.")
    if hasattr(locked_session, "payable_item"):
        return locked_session.payable_item
    engagement = (
        Engagement.objects.filter(
            worker__user=locked_session.specialist,
            assignments__center=locked_session.center,
            assignments__is_active=True,
            starts_on__lte=locked_session.scheduled_start.date(),
            status__in=[Engagement.Status.READY, Engagement.Status.ACTIVE, Engagement.Status.ONBOARDING],
        )
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=locked_session.scheduled_start.date()))
        .distinct()
        .first()
    )
    if engagement is None:
        raise ValidationError("No active ClearCode engagement covers this specialist and center.")
    if actor.id != engagement.worker.user_id and not is_global_admin(actor):
        _require_role(actor, engagement.payer, WorkforceRoleMembership.Role.WORKFORCE_ADMIN)
    service_date = locked_session.scheduled_start.date()
    rate = _effective_rate(engagement=engagement, center=locked_session.center, service_date=service_date)
    if rate is None:
        raise ValidationError("No approved rate covers this session date and center.")
    if rate.unit == RateSchedule.Unit.HOUR:
        if not locked_session.started_at or not locked_session.ended_at:
            raise ValidationError("Hourly session pay requires start and end times.")
        seconds = Decimal(str((locked_session.ended_at - locked_session.started_at).total_seconds()))
        units = (seconds / Decimal("3600")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        units = Decimal("1.00")
    gross = (units * rate.amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    payable = PayableItem(
        engagement=engagement,
        center=locked_session.center,
        source_session=locked_session,
        service_date=service_date,
        description=f"Completed reading session {locked_session.pk}",
        units=units,
        rate=rate,
        gross_amount=gross,
        status=PayableItem.Status.SUBMITTED,
        created_by=actor,
    )
    payable.full_clean()
    payable.save()
    _audit(
        actor=actor,
        action="workforce.payable_submitted",
        instance=payable,
        after={"center_id": payable.center_id, "service_date": str(service_date), "gross_amount": str(gross)},
    )
    return payable


@transaction.atomic
def approve_rate(*, rate: RateSchedule, actor) -> RateSchedule:
    _require_role(actor, rate.engagement.payer, WorkforceRoleMembership.Role.WORKFORCE_ADMIN)
    locked = RateSchedule.objects.select_for_update().get(pk=rate.pk)
    if locked.status != RateSchedule.Status.DRAFT:
        raise ValidationError("Only a draft rate can be approved.")
    locked.status = RateSchedule.Status.APPROVED
    locked.approved_by = actor
    locked.approved_at = timezone.now()
    locked.full_clean()
    locked.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    _audit(actor=actor, action="workforce.rate_approved", instance=locked, after={"status": locked.status})
    return locked


@transaction.atomic
def approve_payable(*, payable: PayableItem, actor) -> PayableItem:
    locked = PayableItem.objects.select_for_update().select_related("center", "engagement__payer").get(pk=payable.pk)
    is_center_approver = actor.school_memberships.filter(
        school=locked.center,
        role__in=[SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN],
        is_deleted=False,
    ).exists()
    if not is_center_approver and not has_workforce_role(
        actor, locked.engagement.payer, WorkforceRoleMembership.Role.WORKFORCE_ADMIN
    ):
        raise PermissionDenied("Center operations approval is required.")
    if locked.status != PayableItem.Status.SUBMITTED:
        raise ValidationError("Only a submitted payable can be approved.")
    locked.status = PayableItem.Status.APPROVED
    locked.approved_by = actor
    locked.approved_at = timezone.now()
    locked.full_clean()
    locked.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    _audit(actor=actor, action="workforce.payable_approved", instance=locked, after={"status": locked.status})
    return locked


@transaction.atomic
def add_payables_to_run(*, payment_run: PaymentRun, payables: Iterable[PayableItem], actor) -> PaymentRun:
    _require_role(actor, payment_run.payer, WorkforceRoleMembership.Role.FINANCE_PREPARER)
    locked_run = PaymentRun.objects.select_for_update().get(pk=payment_run.pk)
    if locked_run.status != PaymentRun.Status.DRAFT:
        raise ValidationError("Payables can be added only to a draft payment run.")
    for payable in PayableItem.objects.select_for_update().filter(pk__in=[item.pk for item in payables]):
        if payable.engagement.payer_id != locked_run.payer_id:
            raise ValidationError("All payables must belong to the payment run payer.")
        if payable.status != PayableItem.Status.APPROVED:
            raise ValidationError(f"Payable {payable.pk} is not approved.")
        readiness = payment_readiness(payable.engagement)
        if not readiness.ready:
            raise ValidationError({f"payable_{payable.pk}": list(readiness.blockers)})
        Payment.objects.create(
            payment_run=locked_run,
            payable=payable,
            engagement=payable.engagement,
            amount=payable.gross_amount,
        )
        payable.status = PayableItem.Status.IN_RUN
        payable.save(update_fields=["status", "updated_at"])
    _audit(
        actor=actor,
        action="workforce.payment_run_funded",
        instance=locked_run,
        after={"payment_count": locked_run.payments.count()},
    )
    return locked_run


@transaction.atomic
def review_payment_run(*, payment_run: PaymentRun, actor) -> PaymentRun:
    _require_role(actor, payment_run.payer, WorkforceRoleMembership.Role.FINANCE_PREPARER)
    locked = PaymentRun.objects.select_for_update().get(pk=payment_run.pk)
    if locked.status != PaymentRun.Status.DRAFT or not locked.payments.exists():
        raise ValidationError("A non-empty draft payment run is required for review.")
    locked.status = PaymentRun.Status.REVIEWED
    locked.reviewed_by = actor
    locked.reviewed_at = timezone.now()
    locked.full_clean()
    locked.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    _audit(actor=actor, action="workforce.payment_run_reviewed", instance=locked, after={"status": locked.status})
    return locked


@transaction.atomic
def approve_payment_run(*, payment_run: PaymentRun, actor) -> PaymentRun:
    _require_role(actor, payment_run.payer, WorkforceRoleMembership.Role.FINANCE_APPROVER)
    locked = PaymentRun.objects.select_for_update().get(pk=payment_run.pk)
    if locked.status != PaymentRun.Status.REVIEWED:
        raise ValidationError("A reviewed payment run is required for approval.")
    locked.status = PaymentRun.Status.APPROVED
    locked.approved_by = actor
    locked.approved_at = timezone.now()
    locked.full_clean()
    locked.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    _audit(actor=actor, action="workforce.payment_run_approved", instance=locked, after={"status": locked.status})
    return locked


def submit_payment_run(
    *, payment_run: PaymentRun, actor, adapter: WorkforceProviderAdapter | None = None
) -> PaymentRun:
    _require_role(actor, payment_run.payer, WorkforceRoleMembership.Role.FINANCE_PREPARER)
    with transaction.atomic():
        locked = PaymentRun.objects.select_for_update().get(pk=payment_run.pk)
        if locked.status == PaymentRun.Status.SUBMITTED:
            return locked
        if locked.status not in [PaymentRun.Status.APPROVED, PaymentRun.Status.SUBMITTING]:
            raise ValidationError("Final approval is required before provider submission.")
        if locked.status == PaymentRun.Status.APPROVED:
            locked.status = PaymentRun.Status.SUBMITTING
            locked.save(update_fields=["status", "updated_at"])
    adapter = adapter or get_workforce_provider_adapter()
    # The provider must honor PaymentRun.idempotency_key. A retry from SUBMITTING
    # repeats the same provider request without creating a second batch.
    submission = adapter.submit_payment_run(payment_run=locked)
    with transaction.atomic():
        locked = PaymentRun.objects.select_for_update().get(pk=payment_run.pk)
        if locked.status == PaymentRun.Status.SUBMITTED:
            return locked
        if locked.status != PaymentRun.Status.SUBMITTING:
            raise ValidationError("Payment run changed while the provider request was in progress.")
        locked.status = PaymentRun.Status.SUBMITTED
        locked.external_batch_id = submission.external_batch_id
        locked.save(update_fields=["status", "external_batch_id", "updated_at"])
        for payment in locked.payments.select_for_update():
            payment.status = Payment.Status.SUBMITTED
            payment.external_payment_id = submission.external_payment_ids.get(payment.pk, "")
            payment.save(update_fields=["status", "external_payment_id", "updated_at"])
        _audit(
            actor=actor,
            action="workforce.payment_run_submitted",
            instance=locked,
            after={"provider": adapter.provider, "status": locked.status},
        )
    return locked


@transaction.atomic
def record_provider_event(*, provider: str, body: bytes, signature: str, adapter=None) -> tuple[ProviderEvent, bool]:
    adapter = adapter or get_workforce_provider_adapter()
    if adapter.provider != provider:
        raise ValidationError("Webhook provider does not match the configured adapter.")
    normalized = adapter.normalize_webhook(body=body, signature=signature)
    event, created = ProviderEvent.objects.get_or_create(
        provider=provider,
        external_event_id=normalized.external_event_id,
        defaults={
            "event_type": normalized.event_type,
            "payload_hash": hashlib.sha256(body).hexdigest(),
            "status": ProviderEvent.Status.PROCESSED,
            "processed_at": timezone.now(),
        },
    )
    if not created:
        return event, False

    if normalized.event_type.startswith("onboarding."):
        onboarding = ProviderOnboarding.objects.select_for_update().filter(
            provider=provider,
            external_onboarding_id=normalized.object_id,
        ).first()
        if onboarding and normalized.status in ProviderOnboarding.Status.values:
            onboarding.status = normalized.status
            onboarding.last_synced_at = timezone.now()
            onboarding.save(update_fields=["status", "last_synced_at", "updated_at"])
        else:
            event.status = ProviderEvent.Status.REJECTED
            event.error_code = "unsupported_or_unknown_onboarding"
            event.save(update_fields=["status", "error_code", "updated_at"])
        return event, True

    if normalized.event_type.startswith("payment."):
        payment = Payment.objects.select_for_update().select_related("payable", "engagement", "payment_run").filter(
            external_payment_id=normalized.object_id
        ).first()
        if payment and normalized.status in Payment.Status.values:
            payment.status = normalized.status
            payment.save(update_fields=["status", "updated_at"])
            if normalized.status == Payment.Status.SETTLED:
                payment.payable.status = PayableItem.Status.PAID
                payment.payable.save(update_fields=["status", "updated_at"])
                tax_year = timezone.localdate().year
                settled_total = sum(
                    (
                        item.amount
                        for item in payment.engagement.payments.filter(
                            status=Payment.Status.SETTLED,
                            updated_at__year=tax_year,
                        )
                    ),
                    Decimal("0.00"),
                )
                florida_threshold = Decimal(
                    str(getattr(settings, "WORKFORCE_FLORIDA_REPORTING_THRESHOLD", "600.00"))
                )
                if (
                    payment.engagement.work_state == "FL"
                    and settled_total >= florida_threshold
                    and payment.engagement.first_reportable_payment_on is None
                ):
                    payment.engagement.first_reportable_payment_on = timezone.localdate()
                    payment.engagement.save(update_fields=["first_reportable_payment_on", "updated_at"])
                    ensure_florida_reporting_task(payment.engagement)
                try:
                    refresh_tax_year_summary(engagement=payment.engagement, tax_year=tax_year)
                except ValidationError:
                    ComplianceTask.objects.update_or_create(
                        engagement=payment.engagement,
                        kind=ComplianceTask.Kind.FEDERAL_1099,
                        tax_year=tax_year,
                        defaults={"status": ComplianceTask.Status.BLOCKED},
                    )
            run_payments = payment.payment_run.payments.all()
            if run_payments.exists() and not run_payments.exclude(status=Payment.Status.SETTLED).exists():
                payment.payment_run.status = PaymentRun.Status.SETTLED
                payment.payment_run.save(update_fields=["status", "updated_at"])
            elif run_payments.filter(status=Payment.Status.FAILED).exists():
                payment.payment_run.status = PaymentRun.Status.FAILED
                payment.payment_run.save(update_fields=["status", "updated_at"])
        else:
            event.status = ProviderEvent.Status.REJECTED
            event.error_code = "unsupported_or_unknown_payment"
            event.save(update_fields=["status", "error_code", "updated_at"])
        return event, True

    event.status = ProviderEvent.Status.REJECTED
    event.error_code = "unsupported_event_type"
    event.save(update_fields=["status", "error_code", "updated_at"])
    return event, created


def federal_threshold_for_year(tax_year: int) -> Decimal:
    setting_name = f"WORKFORCE_FEDERAL_1099_THRESHOLD_{tax_year}"
    configured = getattr(settings, setting_name, None)
    if configured is not None:
        return Decimal(str(configured))
    raise ValidationError("Configure and review the federal 1099 threshold for this tax year before filing.")


def refresh_tax_year_summary(*, engagement: Engagement, tax_year: int) -> TaxYearSummary:
    threshold = federal_threshold_for_year(tax_year)
    total = sum(
        (
            payment.amount
            for payment in engagement.payments.filter(
                status=Payment.Status.SETTLED,
                updated_at__year=tax_year,
            )
        ),
        Decimal("0.00"),
    )
    summary, _ = TaxYearSummary.objects.update_or_create(
        engagement=engagement,
        tax_year=tax_year,
        defaults={
            "total_paid": total,
            "filing_threshold": threshold,
            "filing_required": total >= threshold,
            "status": TaxYearSummary.Status.READY_TO_FILE if total >= threshold else TaxYearSummary.Status.TRACKING,
        },
    )
    return summary
