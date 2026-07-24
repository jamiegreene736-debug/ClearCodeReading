from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.decision_support.models import Flag, OutcomeAggregate, Prediction
    from apps.sessions.models import Session
    from apps.users.models import ChildProfile


def evaluate_flags_for_session(session: "Session") -> list["Flag"]:
    """Return no flags until the V2 deterministic evaluation engine is implemented."""
    return []


def generate_basic_prediction(child: "ChildProfile") -> "Prediction | None":
    """Return no estimate until the V2 prediction engine is implemented."""
    return None


def run_outcomes_aggregation(period: object) -> list["OutcomeAggregate"]:
    """Return no aggregates until the V2 de-identified aggregation engine is implemented."""
    return []
