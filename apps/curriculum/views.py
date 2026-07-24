from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import IsEvaluator, has_coppa_consent, user_can_evaluate_child
from apps.curriculum.models import (
    Curriculum,
    CurriculumSequence,
    Lesson,
    PlacementEvidence,
    PlacementRecommendation,
    SequencePlan,
    Skill,
    SkillCrosswalk,
    StudentPlacement,
    TeachingAid,
)
from apps.curriculum.placement import confirm_recommendation, generate_recommendation
from apps.curriculum.serializers import (
    ConfirmPlacementRecommendationSerializer,
    CurriculumSequenceSerializer,
    CurriculumSerializer,
    LessonSerializer,
    PlacementEvidenceSerializer,
    PlacementRecommendationSerializer,
    SequencePlanSerializer,
    SkillSerializer,
    SkillCrosswalkSerializer,
    StudentPlacementSerializer,
    TeachingAidSerializer,
    UpdateSequencePlanItemSerializer,
)
from apps.progress.models import Progress
from apps.users.models import AuditLog, ChildProfile, CustomUser


def _center_scope(user):
    if not getattr(user, "is_authenticated", False):
        return Q(pk__in=[])
    if getattr(user, "is_superuser", False) or getattr(user, "role", None) == CustomUser.Role.SUPER_ADMIN:
        return Q()
    return Q(center__memberships__user=user, center__memberships__is_deleted=False)


def _crosswalk_scope(user):
    if getattr(user, "is_superuser", False) or getattr(user, "role", None) == CustomUser.Role.SUPER_ADMIN:
        return Q()
    membership = Q(center__memberships__user=user, center__memberships__is_deleted=False)
    global_for_member_center = Q(
        center__isnull=True,
        skill_node_a__center__memberships__user=user,
        skill_node_a__center__memberships__is_deleted=False,
    )
    return membership | global_for_member_center


class SkillViewSet(viewsets.ModelViewSet):
    serializer_class = SkillSerializer
    permission_classes = [IsAuthenticated]
    queryset = Skill.objects.filter(is_deleted=False).prefetch_related("prerequisites")


class LessonViewSet(viewsets.ModelViewSet):
    serializer_class = LessonSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Lesson.objects.filter(is_deleted=False).select_related("skill").prefetch_related("teaching_aids")
        if self.request.query_params.get("published") == "true":
            queryset = queryset.filter(is_published=True)
        grade_level = self.request.query_params.get("grade_level")
        skill_id = self.request.query_params.get("skill")
        if grade_level:
            queryset = queryset.filter(grade_level=grade_level)
        if skill_id:
            queryset = queryset.filter(skill_id=skill_id)
        return queryset

    @action(detail=False, methods=["get"], url_path="personalized")
    def personalized(self, request):
        child_id = request.query_params.get("child")
        if not child_id:
            return Response({"child": "This query parameter is required."}, status=400)

        child = ChildProfile.objects.select_related("school").get(id=child_id, is_deleted=False)
        if not has_coppa_consent(child):
            return Response({"detail": "COPPA consent is required before personalizing lessons."}, status=403)

        mastered_skill_ids = Progress.objects.filter(
            child=child,
            status=Progress.Status.MASTERED,
            is_deleted=False,
        ).values_list("skill_id", flat=True)
        developing_skill_ids = Progress.objects.filter(
            child=child,
            status__in=[Progress.Status.NOT_STARTED, Progress.Status.EMERGING, Progress.Status.DEVELOPING],
            is_deleted=False,
        ).values_list("skill_id", flat=True)

        queryset = self.get_queryset().filter(is_published=True)
        if developing_skill_ids:
            queryset = queryset.filter(skill_id__in=developing_skill_ids)
        else:
            queryset = queryset.exclude(skill_id__in=mastered_skill_ids)
        if child.grade_level:
            queryset = queryset.filter(grade_level__in=["", child.grade_level])

        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(LessonSerializer(page, many=True, context=self.get_serializer_context()).data)
        return Response(LessonSerializer(queryset, many=True, context=self.get_serializer_context()).data)


class TeachingAidViewSet(viewsets.ModelViewSet):
    serializer_class = TeachingAidSerializer
    permission_classes = [IsAuthenticated]
    queryset = TeachingAid.objects.filter(is_deleted=False).select_related("lesson", "skill")


class CurriculumViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CurriculumSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Curriculum.objects.filter(is_deleted=False).filter(_center_scope(self.request.user)).distinct()


class CurriculumSequenceViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CurriculumSequenceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = (
            CurriculumSequence.objects.filter(is_deleted=False)
            .filter(_center_scope(self.request.user))
            .select_related("center", "curriculum")
            .prefetch_related("prerequisites")
            .distinct()
        )
        curriculum_id = self.request.query_params.get("curriculum")
        if curriculum_id:
            queryset = queryset.filter(curriculum_id=curriculum_id)
        return queryset


class SkillCrosswalkViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SkillCrosswalkSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            SkillCrosswalk.objects.filter(is_deleted=False)
            .filter(_crosswalk_scope(self.request.user))
            .select_related(
                "center",
                "skill_node_a__curriculum",
                "skill_node_b__curriculum",
            )
            .distinct()
        )


class PlacementEvidenceViewSet(viewsets.ModelViewSet):
    serializer_class = PlacementEvidenceSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return (
            PlacementEvidence.objects.filter(is_deleted=False)
            .filter(_center_scope(self.request.user))
            .select_related("center", "child", "curriculum", "administered_by", "source_assessment")
            .distinct()
        )

    @action(detail=True, methods=["post"], url_path="recommend")
    def recommend(self, request, pk=None):
        evidence = self.get_object()
        if not user_can_evaluate_child(request.user, evidence.child):
            return Response({"detail": "You are not assigned to this child's center."}, status=status.HTTP_403_FORBIDDEN)
        if evidence.status != PlacementEvidence.Status.COMPLETED:
            return Response(
                {"detail": "Complete the structured placement evidence before generating a recommendation."},
                status=status.HTTP_409_CONFLICT,
            )
        existing = getattr(evidence, "recommendation", None)
        if existing and existing.status != PlacementRecommendation.Status.PENDING:
            return Response(
                {"detail": "A finalized specialist decision cannot be regenerated."},
                status=status.HTTP_409_CONFLICT,
            )
        recommendation = generate_recommendation(evidence)
        AuditLog.objects.create(
            actor=request.user,
            action="placement.recommendation_generated",
            entity_type="PlacementRecommendation",
            entity_id=str(recommendation.id),
            after={
                "child_id": evidence.child_id,
                "decision": recommendation.decision,
                "recommended_position_id": recommendation.recommended_position_id,
            },
        )
        return Response(
            PlacementRecommendationSerializer(recommendation, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


class PlacementRecommendationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PlacementRecommendationSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return (
            PlacementRecommendation.objects.filter(is_deleted=False)
            .filter(_center_scope(self.request.user))
            .select_related(
                "center",
                "evidence__child",
                "evidence__curriculum",
                "recommended_curriculum",
                "recommended_position",
                "final_curriculum",
                "final_position",
                "resulting_placement__current_position",
            )
            .prefetch_related("recommended_sequence__position")
            .distinct()
        )

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, pk=None):
        recommendation = self.get_object()
        if not user_can_evaluate_child(request.user, recommendation.evidence.child):
            return Response({"detail": "You are not assigned to this child's center."}, status=status.HTTP_403_FORBIDDEN)
        if recommendation.status != PlacementRecommendation.Status.PENDING:
            return Response(
                {"detail": "This recommendation already has a specialist decision."},
                status=status.HTTP_409_CONFLICT,
            )
        input_serializer = ConfirmPlacementRecommendationSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            placement = confirm_recommendation(
                recommendation,
                request.user,
                final_position=input_serializer.validated_data.get("final_position"),
                override_rationale=input_serializer.validated_data.get("override_rationale", ""),
                evidence_considered=input_serializer.validated_data.get("evidence_considered", {}),
                create_sequence_plan=input_serializer.validated_data["create_sequence_plan"],
            )
        except DjangoValidationError as error:
            return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)
        recommendation.refresh_from_db()
        AuditLog.objects.create(
            actor=request.user,
            action=f"placement.recommendation_{recommendation.status}",
            entity_type="PlacementRecommendation",
            entity_id=str(recommendation.id),
            after={
                "child_id": recommendation.evidence.child_id,
                "placement_id": placement.id,
                "final_position_id": recommendation.final_position_id,
                "override_labeled": recommendation.status == PlacementRecommendation.Status.OVERRIDDEN,
            },
        )
        return Response(self.get_serializer(recommendation).data)

    @action(detail=False, methods=["get"], url_path="grouping-suggestions")
    def grouping_suggestions(self, request):
        placements = (
            StudentPlacement.objects.filter(is_active=True, is_deleted=False)
            .filter(_center_scope(request.user))
            .select_related("child", "curriculum", "current_position")
            .distinct()
            .order_by("curriculum__code", "current_position__sequence_order", "child__last_name")
        )
        groups = {}
        for placement in placements:
            key = f"{placement.curriculum.code}:{placement.current_position.code}"
            group = groups.setdefault(
                key,
                {
                    "methodology": placement.curriculum.code,
                    "curriculum": placement.curriculum_id,
                    "position": placement.current_position_id,
                    "position_code": placement.current_position.code,
                    "students": [],
                },
            )
            child = placement.child
            group["students"].append(
                {
                    "child": child.id,
                    "display_name": str(child),
                    "availability_windows": child.availability_windows,
                    "services_authorized": child.idea_services_authorized,
                }
            )
        return Response({"groups": list(groups.values())})


class StudentPlacementViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = StudentPlacementSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return (
            StudentPlacement.objects.filter(is_deleted=False)
            .filter(_center_scope(self.request.user))
            .select_related("center", "child", "curriculum", "current_position", "placed_by")
            .prefetch_related("override_history")
            .distinct()
        )


class SequencePlanViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SequencePlanSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return (
            SequencePlan.objects.filter(is_deleted=False)
            .filter(_center_scope(self.request.user))
            .select_related(
                "center",
                "placement__child",
                "placement__curriculum",
                "placement__current_position",
                "created_from_recommendation",
            )
            .prefetch_related("items__position__curriculum")
            .distinct()
        )

    @action(detail=True, methods=["patch"], url_path=r"items/(?P<item_id>[^/.]+)")
    def update_item(self, request, pk=None, item_id=None):
        plan = self.get_object()
        item = plan.items.filter(pk=item_id).select_related("position", "plan__placement").first()
        if item is None:
            return Response({"detail": "Sequence plan item not found."}, status=status.HTTP_404_NOT_FOUND)
        input_serializer = UpdateSequencePlanItemSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        for field, value in input_serializer.validated_data.items():
            setattr(item, field, value)
        item.full_clean()
        item.save(update_fields=[*input_serializer.validated_data.keys(), "updated_at"])
        plan._prefetched_objects_cache.pop("items", None)
        return Response(SequencePlanSerializer(plan, context=self.get_serializer_context()).data)
