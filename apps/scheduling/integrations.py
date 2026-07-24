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

    def upsert_booking(self, booking) -> str:
        """Create or update by the local booking's stable identity."""
        ...

    def pull_bookings(self, updated_since) -> list[dict]:
        """Return remote changes for idempotent inbound reconciliation."""
        ...


class SchedulerNotConfigured(RuntimeError):
    pass


def get_scheduler_adapter() -> SchedulerAdapter:
    from django.conf import settings
    from django.utils.module_loading import import_string

    adapter_path = getattr(settings, "SCHEDULER_ADAPTER", "")
    if not adapter_path:
        raise SchedulerNotConfigured("Configure Jane App or Acuity before syncing approved schedules.")
    return import_string(adapter_path)()
