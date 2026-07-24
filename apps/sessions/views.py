from django.db.models import F, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import IsEvaluator, user_can_evaluate_child
from apps.curriculum.models import StudentPlacement
from apps.sessions.models import Session
from apps.sessions.serializers import SessionSerializer
from apps.users.models import ChildProfile, CustomUser


def _accessible_center_filter(user):
    if not getattr(user, "is_authenticated", False):
        return Q(pk__in=[])
    if getattr(user, "is_superuser", False) or getattr(user, "role", None) == CustomUser.Role.SUPER_ADMIN:
        return Q()
    return Q(center__memberships__user=user, center__memberships__is_deleted=False)


class SessionViewSet(viewsets.ModelViewSet):
    serializer_class = SessionSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        queryset = (
            Session.objects.filter(is_deleted=False)
            .filter(_accessible_center_filter(self.request.user))
            .select_related("center", "child", "specialist", "curriculum_position__curriculum")
            .prefetch_related("targeted_positions", "revision_history")
            .distinct()
        )
        child_id = self.request.query_params.get("child")
        if child_id:
            queryset = queryset.filter(child_id=child_id)
        return queryset

    @action(detail=False, methods=["get"], url_path="defaults")
    def defaults(self, request):
        child_id = request.query_params.get("child")
        if not child_id:
            return Response({"child": "This query parameter is required."}, status=status.HTTP_400_BAD_REQUEST)
        child = ChildProfile.objects.select_related("school").filter(pk=child_id, is_deleted=False).first()
        if child is None:
            return Response({"detail": "Child not found."}, status=status.HTTP_404_NOT_FOUND)
        if not user_can_evaluate_child(request.user, child):
            return Response({"detail": "You are not assigned to this child's center."}, status=status.HTTP_403_FORBIDDEN)
        placement = (
            StudentPlacement.objects.filter(child=child, is_active=True, is_deleted=False)
            .select_related("curriculum", "current_position__curriculum")
            .first()
        )
        if placement is None:
            return Response({"detail": "This child does not have an active placement."}, status=status.HTTP_409_CONFLICT)
        position = placement.current_position
        intervention_part = SessionSerializer._default_intervention_part(child, position)
        return Response(
            {
                "child": child.id,
                "center": placement.center_id,
                "curriculum": placement.curriculum_id,
                "methodology": placement.curriculum.code,
                "curriculum_position": position.id,
                "position_code": position.code,
                "targeted_positions": [position.id],
                "intervention_part": intervention_part,
                "scheduled_start": timezone.now(),
                "suggested_activity_codes": position.activities,
                "item_set_schema": position.item_set_schema,
                "mastery_criteria": position.mastery_criteria,
            }
        )

    @action(detail=False, methods=["get"], url_path="logging-metrics")
    def logging_metrics(self, request):
        queryset = self.get_queryset().filter(status=Session.Status.COMPLETED)
        total = queryset.count()
        same_day = (
            queryset.exclude(ended_at__isnull=True)
            .annotate(logged_date=TruncDate("created_at"), completed_date=TruncDate("ended_at"))
            .filter(logged_date=F("completed_date"))
            .count()
        )
        return Response(
            {
                "completed_sessions": total,
                "same_day_logged": same_day,
                "same_day_rate": round((same_day / total) * 100, 2) if total else None,
                "target_rate": 95,
            }
        )
