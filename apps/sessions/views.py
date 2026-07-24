from django.db.models import F, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import IsEvaluator, user_can_evaluate_child
from apps.curriculum.models import StudentPlacement
from apps.sessions.models import Session, SessionTemplate, SkillObservation
from apps.sessions.serializers import SessionSerializer, SessionTemplateSerializer, SkillObservationSerializer
from apps.sessions.services import capture_defaults, resolve_session_template
from apps.users.models import ChildProfile, CustomUser


def _accessible_center_filter(user):
    if not getattr(user, "is_authenticated", False):
        return Q(pk__in=[])
    if getattr(user, "is_superuser", False) or getattr(user, "role", None) == CustomUser.Role.SUPER_ADMIN:
        return Q()
    return Q(center__memberships__user=user, center__memberships__is_deleted=False)


class IsSessionTemplateManager(IsEvaluator):
    """Evaluator role plus the view's center-scoped object lookup."""

    def has_object_permission(self, request, view, obj):
        return True


class SessionViewSet(viewsets.ModelViewSet):
    serializer_class = SessionSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        queryset = (
            Session.objects.filter(is_deleted=False)
            .filter(_accessible_center_filter(self.request.user))
            .select_related(
                "center",
                "child",
                "specialist",
                "curriculum_position__curriculum",
                "session_template",
            )
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
        session_template = resolve_session_template(position, intervention_part)
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
                "session_template": session_template.id if session_template else None,
                "session_template_title": session_template.title if session_template else None,
                "session_template_version": session_template.version if session_template else None,
                "capture_fields": session_template.capture_fields if session_template else {},
                "capture_defaults": capture_defaults(session_template),
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


class SessionTemplateViewSet(viewsets.ModelViewSet):
    serializer_class = SessionTemplateSerializer
    permission_classes = [IsAuthenticated, IsSessionTemplateManager]

    def get_queryset(self):
        queryset = (
            SessionTemplate.objects.filter(is_deleted=False)
            .filter(_accessible_center_filter(self.request.user))
            .select_related("center", "curriculum", "curriculum_position")
            .distinct()
        )
        filters = {
            "curriculum_id": self.request.query_params.get("curriculum"),
            "curriculum_position_id": self.request.query_params.get("curriculum_position"),
            "session_part": self.request.query_params.get("session_part"),
        }
        for field_name, value in filters.items():
            if value:
                queryset = queryset.filter(**{field_name: value})
        if self.request.query_params.get("active") == "true":
            queryset = queryset.filter(is_active=True)
        return queryset

    def perform_destroy(self, instance):
        instance.updated_by = self.request.user
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.save(update_fields=["updated_by", "is_deleted", "deleted_at", "updated_at"])


class SkillObservationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SkillObservationSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        queryset = (
            SkillObservation.objects.filter(is_deleted=False)
            .filter(_accessible_center_filter(self.request.user))
            .select_related("center", "session", "child", "curriculum_position")
            .distinct()
        )
        filters = {
            "center_id": self.request.query_params.get("center"),
            "child_id": self.request.query_params.get("child"),
            "curriculum_position_id": self.request.query_params.get("curriculum_position"),
            "session_id": self.request.query_params.get("session"),
        }
        for field_name, value in filters.items():
            if value:
                queryset = queryset.filter(**{field_name: value})
        return queryset
