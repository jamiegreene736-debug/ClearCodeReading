from django.db.models import Avg, Count
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import COPPAConsentRequired, has_coppa_consent
from apps.progress.models import MasteryRecord, Progress
from apps.progress.serializers import MasteryRecordSerializer, ProgressSerializer
from apps.users.models import ChildProfile


class ProgressViewSet(viewsets.ModelViewSet):
    serializer_class = ProgressSerializer
    permission_classes = [IsAuthenticated, COPPAConsentRequired]

    def get_queryset(self):
        queryset = Progress.objects.filter(is_deleted=False).select_related("child", "skill", "school", "last_assessment")
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

        child = ChildProfile.objects.get(id=child_id, is_deleted=False)
        if not has_coppa_consent(child):
            return Response({"detail": "COPPA consent is required before viewing the progress dashboard."}, status=403)

        records = self.get_queryset().filter(child=child)
        by_status = records.values("status").annotate(count=Count("id")).order_by("status")
        by_domain = records.values("skill__domain").annotate(count=Count("id"), average_score=Avg("current_score")).order_by("skill__domain")
        recent_mastery = MasteryRecord.objects.filter(child=child, is_deleted=False).select_related("skill").order_by("-mastered_at")[:10]

        return Response(
            {
                "child": child_id,
                "summary": {
                    "total_skills": records.count(),
                    "mastered": records.filter(status=Progress.Status.MASTERED).count(),
                    "developing": records.filter(status__in=[Progress.Status.EMERGING, Progress.Status.DEVELOPING]).count(),
                },
                "by_status": list(by_status),
                "by_domain": list(by_domain),
                "recent_mastery": MasteryRecordSerializer(recent_mastery, many=True, context=self.get_serializer_context()).data,
            }
        )


class MasteryRecordViewSet(viewsets.ModelViewSet):
    serializer_class = MasteryRecordSerializer
    permission_classes = [IsAuthenticated, COPPAConsentRequired]

    def get_queryset(self):
        queryset = MasteryRecord.objects.filter(is_deleted=False).select_related("child", "skill", "progress", "assessment", "mastered_by")
        child_id = self.request.query_params.get("child")
        if child_id:
            queryset = queryset.filter(child_id=child_id)
        return queryset
