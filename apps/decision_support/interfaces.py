from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from apps.decision_support.models import GrowthFlag, MilestonePrediction


class DecisionSupportEngine(Protocol):
    """Stable boundary for deterministic or future in-house advisory engines."""

    def evaluate_completed_session(self, session_id: int) -> list[GrowthFlag]:
        ...

    def generate_milestone_prediction(self, child_id: int) -> MilestonePrediction:
        ...


def get_decision_support_engine() -> DecisionSupportEngine:
    engine_path = getattr(
        settings,
        "DECISION_SUPPORT_ENGINE",
        "apps.decision_support.engine.DeterministicDecisionSupportEngine",
    )
    engine_class = import_string(engine_path)
    engine = engine_class()
    required = ("evaluate_completed_session", "generate_milestone_prediction")
    if any(not hasattr(engine, method) for method in required):
        raise ImproperlyConfigured(f"DECISION_SUPPORT_ENGINE must implement {', '.join(required)}.")
    return engine
