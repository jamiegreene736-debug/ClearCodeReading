from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class AdvisoryOutput:
    text: str
    provider: str
    model: str
    metadata: dict


class InstructionalAIService(Protocol):
    """Stable provider boundary. Output is advisory and never mutates records."""

    def placement_narrative(self, context: dict) -> AdvisoryOutput | None:
        ...


class InstructionalAIError(RuntimeError):
    """Provider adapters raise this when advisory generation cannot complete safely."""


class DisabledInstructionalAIService:
    """Safe default used until a reviewed external or in-house provider is configured."""

    def placement_narrative(self, context: dict) -> AdvisoryOutput | None:
        return None


def get_instructional_ai_service() -> InstructionalAIService:
    service_path = getattr(
        settings,
        "INSTRUCTIONAL_AI_SERVICE",
        "apps.ai.services.DisabledInstructionalAIService",
    )
    service_class = import_string(service_path)
    service = service_class()
    if not hasattr(service, "placement_narrative"):
        raise ImproperlyConfigured("INSTRUCTIONAL_AI_SERVICE must implement placement_narrative(context).")
    return service
