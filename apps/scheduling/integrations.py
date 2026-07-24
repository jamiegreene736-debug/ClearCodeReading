from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SchedulerStudent:
    external_id: str
    display_name: str
    availability_windows: list[dict]


class SchedulerAdapter(Protocol):
    """Provider-neutral contract for Jane App, Acuity, or another bought scheduler."""

    provider: str

    def upsert_student(self, student: SchedulerStudent) -> str:
        ...

    def availability(self, external_student_id: str) -> list[dict]:
        ...


class SchedulerNotConfigured(RuntimeError):
    pass


def get_scheduler_adapter() -> SchedulerAdapter:
    raise SchedulerNotConfigured("No scheduler provider is configured for Phase 1.")
