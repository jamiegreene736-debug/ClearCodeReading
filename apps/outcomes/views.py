from django.db.models import OuterRef, Subquery
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.outcomes.models import DeIdentifiedOutcomeSnapshot
from apps.outcomes.permissions import IsLeadershipOutcomesUser
from apps.outcomes.serializers import DeIdentifiedOutcomeSnapshotSerializer
from apps.outcomes.services import latest_snapshots_for_user
from apps.users.models import AuditLog


class OutcomeSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DeIdentifiedOutcomeSnapshotSerializer
    permission_classes = [IsLeadershipOutcomesUser]

    def get_queryset(self):
        queryset = latest_snapshots_for_user(self.request.user)
        latest_generated_at = (
            queryset.filter(
                center_id=OuterRef("center_id"),
                methodology=OuterRef("methodology"),
                grade_band=OuterRef("grade_band"),
                window_type=OuterRef("window_type"),
                window_start=OuterRef("window_start"),
                window_end=OuterRef("window_end"),
                metric_scope=OuterRef("metric_scope"),
            )
            .order_by("-generated_at")
            .values("generated_at")[:1]
        )
        queryset = queryset.filter(generated_at=Subquery(latest_generated_at))
        for field in ["methodology", "grade_band", "window_type", "center_key"]:
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset.order_by("-window_end", "center_key", "methodology", "grade_band")

    def list(self, request, *args, **kwargs):
        self._audit(request, action="outcomes.snapshots.list")
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=["get"])
    def trends(self, request):
        self._audit(request, action="outcomes.snapshots.trends")
        queryset = self.get_queryset()
        current = queryset.order_by("-window_end").first()
        if current is None:
            return Response({"current": None, "previous": None, "deltas": {}})
        previous = (
            latest_snapshots_for_user(request.user)
            .filter(
                center_id=current.center_id,
                methodology=current.methodology,
                grade_band=current.grade_band,
                metric_scope=current.metric_scope,
                window_type=current.window_type,
                window_end__lt=current.window_end,
            )
            .order_by("-window_end", "-generated_at")
            .first()
        )
        return Response(
            {
                "current": DeIdentifiedOutcomeSnapshotSerializer(current).data,
                "previous": DeIdentifiedOutcomeSnapshotSerializer(previous).data if previous else None,
                "deltas": _metric_deltas(current, previous),
            }
        )

    def _audit(self, request, action):
        AuditLog.objects.create(
            actor=request.user,
            action=action,
            entity_type="outcomes.DeIdentifiedOutcomeSnapshot",
            metadata={
                "query_params": dict(request.query_params),
                "de_identified": True,
            },
        )


def _metric_deltas(current, previous):
    if previous is None:
        return {}
    deltas = {}
    for key in [
        "skill_mastery_rate",
        "completed_sessions",
        "mean_sessions_to_mastery",
        "median_sessions_to_mastery",
        "mean_sessions_to_position",
        "median_sessions_to_position",
    ]:
        current_value = current.metrics.get(key)
        previous_value = previous.metrics.get(key)
        if isinstance(current_value, (int, float)) and isinstance(previous_value, (int, float)):
            deltas[key] = round(current_value - previous_value, 2)
    return deltas
