import logging
import os
from decimal import Decimal

from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import COPPAConsentRequired, IsEvaluator, has_coppa_consent, is_parent_of_child, user_can_evaluate_child
from apps.assessments.audio import (
    ASSESSMENT_AUDIO,
    AudioGenerationError,
    generate_audio_asset,
    get_elevenlabs_api_key,
)
from apps.assessments.models import Assessment, AssessmentAudioAsset, AssessmentQuestion, ChildAssessmentResponse
from apps.assessments.serializers import (
    AnswerSubmissionSerializer,
    AssessmentQuestionSerializer,
    AssessmentResultSerializer,
    AssessmentSerializer,
    ChildAssessmentResponseSerializer,
    StartSurveySerializer,
)
from apps.assessments.services import compute_and_persist_assessment_result
from apps.assessments.tasks import notify_assessment_review_completed
from apps.notifications.tasks import notify_evaluator_assessment_human_review
from apps.progress.models import Progress
from apps.users.models import AuditLog


logger = logging.getLogger(__name__)


def assessment_audio(request, key):
    audio = AssessmentAudioAsset.objects.filter(key=key).first()
    if audio is None:
        try:
            audio, _ = generate_audio_asset(key)
        except AudioGenerationError as exc:
            logger.warning("Assessment audio unavailable for %s: %s", key, exc)
            if key not in ASSESSMENT_AUDIO:
                raise Http404("Assessment audio not found.")
            response = HttpResponse("Assessment audio is not ready.", status=503, content_type="text/plain")
            response["Cache-Control"] = "no-store"
            response["X-Assessment-Audio-Status"] = "unavailable"
            return response
    response = HttpResponse(bytes(audio.audio), content_type=audio.content_type)
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response["Content-Length"] = str(audio.byte_length)
    return response


def assessment_audio_status(request):
    cached = set(AssessmentAudioAsset.objects.filter(key__in=ASSESSMENT_AUDIO.keys()).values_list("key", flat=True))
    missing = [key for key in ASSESSMENT_AUDIO if key not in cached]
    configured = bool(get_elevenlabs_api_key()) and bool(os.getenv("ELEVENLABS_VOICE_ID"))
    return JsonResponse(
        {
            "configured": configured,
            "cached_count": len(cached),
            "total_count": len(ASSESSMENT_AUDIO),
            "missing": missing,
        }
    )


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

    def _can_start_or_answer_for_child(self, user, child):
        return is_parent_of_child(user, child) or user_can_evaluate_child(user, child)

    def _question_queryset(self, section=None):
        queryset = (
            AssessmentQuestion.objects.filter(is_active=True, is_deleted=False)
            .prefetch_related("question_options")
            .order_by("category", "difficulty", "sort_order", "id")
        )
        if section:
            queryset = queryset.filter(category=section)
        return queryset

    def _score_answer(self, question, selected_option, answer):
        if selected_option is not None:
            return selected_option.is_correct, selected_option.score_value

        correct_answer = question.correct_answer or {}
        if not correct_answer:
            return None, None

        expected = correct_answer.get("value", correct_answer)
        submitted = answer.get("value", answer) if isinstance(answer, dict) else answer
        is_correct = str(submitted).strip().lower() == str(expected).strip().lower()
        return is_correct, Decimal("1") if is_correct else Decimal("0")

    @action(detail=False, methods=["post"], url_path="start-survey", permission_classes=[IsAuthenticated, COPPAConsentRequired])
    def start_survey(self, request):
        serializer = StartSurveySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        child = serializer.validated_data["child"]

        if not self._can_start_or_answer_for_child(request.user, child):
            return Response({"detail": "Only an approved parent/guardian or teacher can start this survey."}, status=status.HTTP_403_FORBIDDEN)
        if not has_coppa_consent(child):
            return Response({"detail": "COPPA consent is required before starting the reading survey."}, status=status.HTTP_403_FORBIDDEN)

        section = serializer.validated_data["first_section"]
        question_limit = serializer.validated_data["question_limit"]
        school = serializer.validated_data.get("school") or child.school

        with transaction.atomic():
            assessment = Assessment.objects.create(
                child=child,
                school=school,
                assigned_by=request.user,
                assessment_type=Assessment.AssessmentType.DIAGNOSTIC,
                status=Assessment.Status.IN_PROGRESS,
                title=serializer.validated_data.get("title") or f"Reading Survey for {child.first_name}",
                started_at=timezone.now(),
                metadata={"source": "reading_survey", "started_by": request.user.id},
            )
            AuditLog.objects.create(
                actor=request.user,
                action="assessment.survey_started",
                entity_type="Assessment",
                entity_id=str(assessment.id),
                after={"child_id": child.id, "section": section},
            )

        questions = self._question_queryset(section=section)[:question_limit]
        return Response(
            {
                "assessment": AssessmentSerializer(assessment, context=self.get_serializer_context()).data,
                "questions": AssessmentQuestionSerializer(questions, many=True).data,
                "section": section,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated, COPPAConsentRequired])
    def questions(self, request, pk=None):
        assessment = self.get_object()
        if not self._can_start_or_answer_for_child(request.user, assessment.child):
            return Response({"detail": "Only an approved parent/guardian or teacher can view survey questions."}, status=status.HTTP_403_FORBIDDEN)

        section = request.query_params.get("section")
        if section and section not in AssessmentQuestion.Category.values:
            return Response({"section": "Unknown survey section."}, status=status.HTTP_400_BAD_REQUEST)

        questions = self._question_queryset(section=section)
        return Response(AssessmentQuestionSerializer(questions, many=True).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, COPPAConsentRequired])
    def answer(self, request, pk=None):
        assessment = self.get_object()
        if assessment.status not in {Assessment.Status.PENDING, Assessment.Status.IN_PROGRESS}:
            return Response({"detail": "Only pending or in-progress surveys can accept answers."}, status=status.HTTP_400_BAD_REQUEST)
        if not self._can_start_or_answer_for_child(request.user, assessment.child):
            return Response({"detail": "Only an approved parent/guardian or teacher can answer this survey."}, status=status.HTTP_403_FORBIDDEN)

        serializer = AnswerSubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        saved_responses = []

        with transaction.atomic():
            assessment.started_at = assessment.started_at or timezone.now()
            assessment.status = Assessment.Status.IN_PROGRESS
            assessment.save(update_fields=["started_at", "status", "updated_at"])

            for answer_data in serializer.validated_data["answers"]:
                question = answer_data["question"]
                selected_option = answer_data.get("selected_option")
                answer_payload = answer_data.get("answer", {})
                is_correct, score_value = self._score_answer(question, selected_option, answer_payload)
                response, _ = ChildAssessmentResponse.objects.update_or_create(
                    assessment=assessment,
                    child=assessment.child,
                    question=question,
                    defaults={
                        "selected_option": selected_option,
                        "answer": answer_payload,
                        "is_correct": is_correct,
                        "score_value": score_value,
                        "time_taken": answer_data.get("time_taken"),
                    },
                )
                saved_responses.append(response)

            AuditLog.objects.create(
                actor=request.user,
                action="assessment.survey_answered",
                entity_type="Assessment",
                entity_id=str(assessment.id),
                after={"answer_count": len(saved_responses), "child_id": assessment.child_id},
            )

        answered_count = ChildAssessmentResponse.objects.filter(assessment=assessment, is_deleted=False).count()
        return Response(
            {
                "assessment": AssessmentSerializer(assessment, context=self.get_serializer_context()).data,
                "responses": ChildAssessmentResponseSerializer(saved_responses, many=True).data,
                "answered_count": answered_count,
            }
        )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, COPPAConsentRequired])
    def complete(self, request, pk=None):
        assessment = self.get_object()
        if assessment.status not in {Assessment.Status.PENDING, Assessment.Status.IN_PROGRESS, Assessment.Status.HUMAN_REVIEW}:
            return Response({"detail": "This survey has already been completed or archived."}, status=status.HTTP_400_BAD_REQUEST)
        if not self._can_start_or_answer_for_child(request.user, assessment.child):
            return Response({"detail": "Only an approved parent/guardian or teacher can complete this survey."}, status=status.HTTP_403_FORBIDDEN)

        response_count = ChildAssessmentResponse.objects.filter(assessment=assessment, is_deleted=False).count()
        if response_count == 0:
            return Response({"detail": "At least one answer is required before completing the survey."}, status=status.HTTP_400_BAD_REQUEST)

        previous_status = assessment.status
        result = compute_and_persist_assessment_result(assessment.id)
        assessment.refresh_from_db()
        if previous_status == Assessment.Status.HUMAN_REVIEW:
            notify_evaluator_assessment_human_review.delay(assessment.id)

        final_message = result.final_scores.get("final_message", f"You are reading at an {result.reading_age}-year-old level")
        return Response(
            {
                "assessment": AssessmentSerializer(assessment, context=self.get_serializer_context()).data,
                "result": AssessmentResultSerializer(result).data,
                "final_message": final_message,
            }
        )

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
