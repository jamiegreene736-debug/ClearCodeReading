from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ReadOnlyModelViewSet

from apps.api.permissions import IsEvaluator
from apps.users.models import CustomUser

from .models import Flag, Milestone, OutcomeAggregate, Prediction
from .permissions import IsLeadership
from .serializers import (
    FlagSerializer,
    MilestoneSerializer,
    OutcomeAggregateSerializer,
    PredictionSerializer,
)


def _accessible_centers(user):
    if not getattr(user, "is_authenticated", False):
        return Q(pk__in=[])
    if user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN:
        return Q()
    return Q(center__memberships__user=user, center__memberships__is_deleted=False)


class FlagViewSet(ReadOnlyModelViewSet):
    serializer_class = FlagSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return (
            Flag.objects.filter(is_deleted=False)
            .filter(_accessible_centers(self.request.user))
            .select_related(
                "center",
                "child",
                "related_session",
                "curriculum_position",
                "routed_to",
                "acknowledged_by",
            )
            .distinct()
        )


class PredictionViewSet(ReadOnlyModelViewSet):
    serializer_class = PredictionSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return (
            Prediction.objects.filter(is_deleted=False)
            .filter(_accessible_centers(self.request.user))
            .select_related("center", "child", "target_milestone", "target_position")
            .distinct()
        )


class MilestoneViewSet(ReadOnlyModelViewSet):
    serializer_class = MilestoneSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return (
            Milestone.objects.filter(is_deleted=False)
            .filter(_accessible_centers(self.request.user))
            .select_related("center", "child", "curriculum_position")
            .distinct()
        )


class OutcomeAggregateViewSet(ReadOnlyModelViewSet):
    serializer_class = OutcomeAggregateSerializer
    permission_classes = [IsAuthenticated, IsLeadership]

    def get_queryset(self):
        return (
            OutcomeAggregate.objects.filter(is_deleted=False)
            .filter(_accessible_centers(self.request.user))
            .select_related("center")
            .distinct()
        )
