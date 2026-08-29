from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Protocol

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class OnboardingInvite:
    external_onboarding_id: str
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class OnboardingState:
    status: str
    remediation_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaymentSubmission:
    external_batch_id: str
    external_payment_ids: Mapping[int, str]


@dataclass(frozen=True)
class NormalizedProviderEvent:
    external_event_id: str
    event_type: str
    object_id: str
    status: str


class WorkforceProviderAdapter(Protocol):
    provider: str

    def create_onboarding_invite(self, *, engagement) -> OnboardingInvite: ...

    def get_onboarding_state(self, *, external_onboarding_id: str) -> OnboardingState: ...

    def submit_payment_run(self, *, payment_run) -> PaymentSubmission: ...

    def normalize_webhook(self, *, body: bytes, signature: str) -> NormalizedProviderEvent: ...


class WorkforceProviderError(RuntimeError):
    pass


class WorkforceProviderNotConfigured(WorkforceProviderError):
    pass


class InvalidProviderWebhook(WorkforceProviderError):
    pass


class StubWorkforceProviderAdapter:
    """Deterministic no-network adapter restricted to development and tests."""

    provider = "stub"

    def create_onboarding_invite(self, *, engagement) -> OnboardingInvite:
        external_id = f"stub-worker-{engagement.pk}"
        return OnboardingInvite(
            external_onboarding_id=external_id,
            url=f"https://stub.invalid/workforce/onboard/{external_id}",
            expires_at=timezone.now() + timedelta(hours=24),
        )

    def get_onboarding_state(self, *, external_onboarding_id: str) -> OnboardingState:
        return OnboardingState(status="invited")

    def submit_payment_run(self, *, payment_run) -> PaymentSubmission:
        return PaymentSubmission(
            external_batch_id=f"stub-run-{payment_run.idempotency_key}",
            external_payment_ids={payment.pk: f"stub-payment-{payment.pk}" for payment in payment_run.payments.all()},
        )

    def normalize_webhook(self, *, body: bytes, signature: str) -> NormalizedProviderEvent:
        if signature != "stub-valid-signature":
            raise InvalidProviderWebhook("Webhook signature is invalid.")
        parts = body.decode("utf-8").split(":", maxsplit=3)
        if len(parts) != 4:
            raise InvalidProviderWebhook("Webhook body is malformed.")
        return NormalizedProviderEvent(
            external_event_id=parts[0],
            event_type=parts[1],
            object_id=parts[2],
            status=parts[3],
        )


def get_workforce_provider_adapter() -> WorkforceProviderAdapter:
    adapter_path = getattr(settings, "WORKFORCE_PROVIDER_ADAPTER", "").strip()
    if not adapter_path:
        raise WorkforceProviderNotConfigured(
            "Choose and configure the workforce payment provider before inviting or paying workers."
        )
    try:
        adapter = import_string(adapter_path)()
    except WorkforceProviderError:
        raise
    except (ImportError, AttributeError, TypeError) as error:
        raise WorkforceProviderNotConfigured("The configured workforce provider adapter could not be loaded.") from error
    if adapter.provider == "stub" and not (
        settings.DEBUG and getattr(settings, "WORKFORCE_ALLOW_STUB_PROVIDER", False)
    ):
        raise WorkforceProviderNotConfigured("The stub workforce provider is restricted to local development and tests.")
    return adapter
