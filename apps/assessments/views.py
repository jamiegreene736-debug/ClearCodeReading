from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import COPPAConsentRequired, IsEvaluator, has_coppa_consent, user_can_evaluate_child
from apps.assessments.models import Assessment
from apps.assessments.serializers import AssessmentSerializer
from apps.assessments.tasks import notify_assessment_review_completed
from apps.progress.models import Progress
from apps.users.models import AuditLog


class DigitalAssessmentSubmissionSerializer(serializers.Serializer):
    responses = serializers.JSONField()
    raw_score = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    max_score = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    scoring = serializers.JSONField(required=False)
    recommendations = serializers.JSONField(required=False)
    metadata = serializers.JSONField(required=False)


class HumanReviewSerializer(serializers.Serializer):
    raw_score = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    max_score = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    percentile = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    scoring = serializers.JSONField(required=False)
    recommendations = serializers.JSONField(required=False)
    reviewer_notes = serializers.CharField(required=False, allow_blank=True)


class StatusTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Assessment.Status.choices)


class AssessmentViewSet(viewsets.ModelViewSet):
    serializer_class = AssessmentSerializer
    permission_classes = [IsAuthenticated, COPPAConsentRequired]

    def get_queryset(self):
        queryset = Assessment.objects.select_related("child", "school", "assigned_by", "skill").filter(is_deleted=False)
        user = self.request.user
        if getattr(user, "role", None) == user.Role.GUARDIAN:
            queryset = queryset.filter(child__guardian_relationships__guardian=user)
        elif not user.is_superuser and getattr(user, "role", None) not in {user.Role.SUPER_ADMIN, user.Role.SCHOOL_ADMIN, user.Role.TEACHER}:
            queryset = queryset.none()

        child_id = self.request.query_params.get("child")
        status_value = self.request.query_params.get("status")
        if child_id:
            queryset = queryset.filter(child_id=child_id)
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset.distinct()

    def perform_create(self, serializer):
        child = serializer.validated_data["child"]
        if not has_coppa_consent(child):
            raise serializers.ValidationError({"child": "COPPA consent is required before assigning assessments."})
        serializer.save(assigned_by=self.request.user, status=Assessment.Status.PENDING)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, COPPAConsentRequired])
    def submit(self, request, pk=None):
        assessment = self.get_object()
        if assessment.status not in {Assessment.Status.PENDING, Assessment.Status.IN_PROGRESS}:
            return Response(
                {"detail": "Only pending or in-progress assessments can be submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = DigitalAssessmentSubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            assessment.responses = serializer.validated_data["responses"]
            assessment.raw_score = serializer.validated_data.get("raw_score", assessment.raw_score)
            assessment.max_score = serializer.validated_data.get("max_score", assessment.max_score)
            assessment.scoring = serializer.validated_data.get("scoring", assessment.scoring)
            assessment.recommendations = serializer.validated_data.get("recommendations", assessment.recommendations)
            assessment.metadata.update(serializer.validated_data.get("metadata", {}))
            assessment.started_at = assessment.started_at or timezone.now()
            assessment.completed_at = timezone.now()
            assessment.status = Assessment.Status.HUMAN_REVIEW
            assessment.save()

            AuditLog.objects.create(
                actor=request.user,
                action="assessment.submitted",
                entity_type="Assessment",
                entity_id=str(assessment.id),
                after={"status": assessment.status, "child_id": assessment.child_id},
            )

        return Response(AssessmentSerializer(assessment, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsEvaluator])
    def review(self, request, pk=None):
        assessment = self.get_object()
        if not user_can_evaluate_child(request.user, assessment.child):
            return Response({"detail": "You are not assigned to evaluate this child."}, status=status.HTTP_403_FORBIDDEN)
        if assessment.status != Assessment.Status.HUMAN_REVIEW:
            return Response(
                {"detail": "Only assessments in human review can be completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = HumanReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            for field in ["raw_score", "max_score", "percentile", "scoring", "recommendations"]:
                if field in serializer.validated_data:
                    setattr(assessment, field, serializer.validated_data[field])
            if serializer.validated_data.get("reviewer_notes"):
                assessment.metadata["reviewer_notes"] = serializer.validated_data["reviewer_notes"]
            assessment.assigned_by = request.user
            assessment.completed_at = timezone.now()
            assessment.status = Assessment.Status.COMPLETED
            assessment.save()

            if assessment.skill_id:
                Progress.objects.update_or_create(
                    child=assessment.child,
                    skill=assessment.skill,
                    defaults={
                        "school": assessment.school or assessment.child.school,
                        "status": Progress.Status.PROFICIENT,
                        "current_score": assessment.raw_score,
                        "last_assessment": assessment,
                        "attempts": 1,
                        "evidence": [{"assessment_id": assessment.id, "reviewed_at": assessment.completed_at.isoformat()}],
                    },
                )

            AuditLog.objects.create(
                actor=request.user,
                action="assessment.review_completed",
                entity_type="Assessment",
                entity_id=str(assessment.id),
                after={"status": assessment.status, "child_id": assessment.child_id},
            )

        notify_assessment_review_completed.delay(assessment.id)
        return Response(AssessmentSerializer(assessment, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsEvaluator])
    def transition(self, request, pk=None):
        assessment = self.get_object()
        serializer = StatusTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        requested_status = serializer.validated_data["status"]

        allowed = {
            Assessment.Status.PENDING: {Assessment.Status.IN_PROGRESS, Assessment.Status.HUMAN_REVIEW, Assessment.Status.ARCHIVED},
            Assessment.Status.IN_PROGRESS: {Assessment.Status.HUMAN_REVIEW, Assessment.Status.ARCHIVED},
            Assessment.Status.HUMAN_REVIEW: {Assessment.Status.COMPLETED, Assessment.Status.ARCHIVED},
            Assessment.Status.COMPLETED: set(),
            Assessment.Status.ARCHIVED: set(),
        }
        if requested_status not in allowed.get(assessment.status, set()):
            return Response(
                {"detail": f"Invalid transition from {assessment.status} to {requested_status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        assessment.status = requested_status
        assessment.save(update_fields=["status", "updated_at"])
        return Response(AssessmentSerializer(assessment, context=self.get_serializer_context()).data)
