from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from apps.api.permissions import IsEvaluator, user_can_evaluate_child
from apps.decision_support.interfaces import get_decision_support_engine
from apps.sessions.models import Session
from apps.users.models import AuditLog, ChildProfile, CustomUser

from .models import (
    Flag,
    GrowthFlag,
    Milestone,
    MilestonePrediction,
    OutcomeAggregate,
    Prediction,
)
from .permissions import IsLeadership
from .serializers import (
    FlagSerializer,
    AcknowledgeFlagSerializer,
    EvaluateSessionSerializer,
    GeneratePredictionSerializer,
    GrowthFlagSerializer,
    MilestoneSerializer,
    MilestonePredictionSerializer,
    OutcomeAggregateSerializer,
    PredictionSerializer,
    ResolveFlagSerializer,
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


class GrowthFlagViewSet(ReadOnlyModelViewSet):
    serializer_class = GrowthFlagSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        queryset = (
            GrowthFlag.objects.filter(is_deleted=False)
            .filter(_accessible_centers(self.request.user))
            .select_related(
                "center",
                "child",
                "position__curriculum",
                "trigger_session",
                "acknowledged_by",
                "resolved_by",
            )
            .prefetch_related("routed_to")
            .distinct()
        )
        status_value = self.request.query_params.get("status")
        severity = self.request.query_params.get("severity")
        child_id = self.request.query_params.get("child")
        if status_value:
            queryset = queryset.filter(status=status_value)
        if severity:
            queryset = queryset.filter(severity=severity)
        if child_id:
            queryset = queryset.filter(child_id=child_id)
        return queryset

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def acknowledge(self, request, pk=None):
        flag = self.get_object()
        serializer = AcknowledgeFlagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if flag.status == GrowthFlag.Status.RESOLVED:
            return Response({"detail": "A resolved flag cannot be acknowledged."}, status=status.HTTP_409_CONFLICT)
        flag.status = GrowthFlag.Status.ACKNOWLEDGED
        flag.acknowledged_by = request.user
        flag.acknowledged_at = timezone.now()
        note = serializer.validated_data.get("note", "").strip()
        if note:
            flag.resolution_note = note
        flag.updated_by = request.user
        flag.save(
            update_fields=[
                "status",
                "acknowledged_by",
                "acknowledged_at",
                "resolution_note",
                "updated_by",
                "updated_at",
            ]
        )
        AuditLog.objects.create(
            actor=request.user,
            action="decision_support.growth_flag_acknowledged",
            entity_type="GrowthFlag",
            entity_id=str(flag.id),
            after={"status": flag.status, "child_id": flag.child_id, "flag_code": flag.flag_code},
        )
        return Response(self.get_serializer(flag).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def resolve(self, request, pk=None):
        flag = self.get_object()
        serializer = ResolveFlagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        flag.status = GrowthFlag.Status.RESOLVED
        flag.resolved_by = request.user
        flag.resolved_at = timezone.now()
        flag.resolution_note = serializer.validated_data["resolution_note"]
        flag.updated_by = request.user
        flag.save(
            update_fields=[
                "status",
                "resolved_by",
                "resolved_at",
                "resolution_note",
                "updated_by",
                "updated_at",
            ]
        )
        AuditLog.objects.create(
            actor=request.user,
            action="decision_support.growth_flag_resolved",
            entity_type="GrowthFlag",
            entity_id=str(flag.id),
            after={"status": flag.status, "child_id": flag.child_id, "flag_code": flag.flag_code},
        )
        return Response(self.get_serializer(flag).data)

    @action(detail=False, methods=["post"], url_path="evaluate-session")
    def evaluate_session(self, request):
        serializer = EvaluateSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = (
            Session.objects.filter(
                pk=serializer.validated_data["session"],
                status=Session.Status.COMPLETED,
                is_deleted=False,
            )
            .filter(_accessible_centers(request.user))
            .select_related("child")
            .distinct()
            .first()
        )
        if session is None:
            return Response({"detail": "Completed session not found."}, status=status.HTTP_404_NOT_FOUND)
        flags = get_decision_support_engine().evaluate_completed_session(session.id)
        return Response(GrowthFlagSerializer(flags, many=True, context=self.get_serializer_context()).data)


class MilestonePredictionViewSet(ReadOnlyModelViewSet):
    serializer_class = MilestonePredictionSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        queryset = (
            MilestonePrediction.objects.filter(is_deleted=False)
            .filter(_accessible_centers(self.request.user))
            .select_related(
                "center",
                "child",
                "placement__current_position",
                "target_position",
            )
            .distinct()
        )
        child_id = self.request.query_params.get("child")
        current = self.request.query_params.get("current")
        if child_id:
            queryset = queryset.filter(child_id=child_id)
        if current == "true":
            queryset = queryset.filter(is_current=True)
        return queryset

    @action(detail=False, methods=["post"])
    def generate(self, request):
        serializer = GeneratePredictionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        child = ChildProfile.objects.select_related("school").filter(
            pk=serializer.validated_data["child"],
            is_deleted=False,
        ).first()
        if child is None:
            return Response({"detail": "Child not found."}, status=status.HTTP_404_NOT_FOUND)
        if not user_can_evaluate_child(request.user, child):
            return Response({"detail": "You are not assigned to this child's center."}, status=status.HTTP_403_FORBIDDEN)
        try:
            prediction = get_decision_support_engine().generate_milestone_prediction(child.id)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
        AuditLog.objects.create(
            actor=request.user,
            action="decision_support.milestone_prediction_generated",
            entity_type="MilestonePrediction",
            entity_id=str(prediction.id),
            after={
                "child_id": child.id,
                "predicted_sessions": prediction.predicted_sessions,
                "predicted_date": prediction.predicted_date.isoformat(),
            },
        )
        return Response(self.get_serializer(prediction).data, status=status.HTTP_201_CREATED)
