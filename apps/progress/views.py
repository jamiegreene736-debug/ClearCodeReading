from django.db.models import Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import COPPAConsentRequired, has_coppa_consent, user_can_evaluate_child
from apps.progress.dashboard import build_parent_dashboard
from apps.progress.models import MasteryRecord, Progress
from apps.progress.serializers import MasteryRecordSerializer, ProgressSerializer
from apps.users.models import ChildProfile, CustomUser, GuardianRelationship


def _visible_children_filter(user):
    if user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN:
        return Q()
    if user.role == CustomUser.Role.GUARDIAN:
        return Q(
            child__guardian_relationships__guardian=user,
            child__guardian_relationships__is_deleted=False,
            child__guardian_relationships__consent_status=GuardianRelationship.ConsentStatus.GRANTED,
        )
    if user.role == CustomUser.Role.STUDENT:
        return Q(child__user=user)
    return Q(child__school__memberships__user=user, child__school__memberships__is_deleted=False)


class ProgressViewSet(viewsets.ModelViewSet):
    serializer_class = ProgressSerializer
    permission_classes = [IsAuthenticated, COPPAConsentRequired]

    def get_queryset(self):
        queryset = Progress.objects.filter(is_deleted=False).filter(_visible_children_filter(self.request.user)).select_related("child", "skill", "school", "last_assessment").distinct()
        child_id = self.request.query_params.get("child")
        skill_id = self.request.query_params.get("skill")
        status_value = self.request.query_params.get("status")
        if child_id:
            queryset = queryset.filter(child_id=child_id)
        if skill_id:
            queryset = queryset.filter(skill_id=skill_id)
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        child_id = request.query_params.get("child")
        if not child_id:
            return Response({"child": "This query parameter is required."}, status=400)

        child = ChildProfile.objects.filter(id=child_id, is_deleted=False).first()
        if child is None:
            return Response({"detail": "Child not found."}, status=404)
        if not has_coppa_consent(child):
            return Response({"detail": "COPPA consent is required before viewing the progress dashboard."}, status=403)
        if request.user.role == CustomUser.Role.GUARDIAN:
            relationship = GuardianRelationship.objects.filter(
                guardian=request.user,
                child=child,
                is_deleted=False,
                consent_status=GuardianRelationship.ConsentStatus.GRANTED,
            ).first()
            if relationship is None or relationship.permissions.get("progress_dashboard") is False:
                return Response({"detail": "This guardian does not have dashboard access for this child."}, status=403)
        elif request.user.role == CustomUser.Role.STUDENT:
            if child.user_id != request.user.id:
                return Response({"detail": "You cannot view another reader's dashboard."}, status=403)
        elif not user_can_evaluate_child(request.user, child):
            return Response({"detail": "You are not assigned to this child's center."}, status=403)
        return Response(build_parent_dashboard(child))


class MasteryRecordViewSet(viewsets.ModelViewSet):
    serializer_class = MasteryRecordSerializer
    permission_classes = [IsAuthenticated, COPPAConsentRequired]

    def get_queryset(self):
        queryset = MasteryRecord.objects.filter(is_deleted=False).filter(_visible_children_filter(self.request.user)).select_related("child", "skill", "progress", "assessment", "mastered_by").distinct()
        child_id = self.request.query_params.get("child")
        if child_id:
            queryset = queryset.filter(child_id=child_id)
        return queryset
