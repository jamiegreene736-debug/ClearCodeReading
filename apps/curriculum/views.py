from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import has_coppa_consent
from apps.curriculum.models import Lesson, Skill, TeachingAid
from apps.curriculum.serializers import LessonSerializer, SkillSerializer, TeachingAidSerializer
from apps.progress.models import Progress
from apps.users.models import ChildProfile


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
