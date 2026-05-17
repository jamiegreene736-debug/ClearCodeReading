from rest_framework import serializers

from apps.api.serializers import COPPAConsentMixin, ChildSummarySerializer, SkillSummarySerializer, UserSummarySerializer
from apps.assessments.serializers import AssessmentSerializer
from apps.progress.models import MasteryRecord, Progress
from apps.schools.serializers import SchoolSerializer


class ProgressSerializer(COPPAConsentMixin, serializers.ModelSerializer):
    child_detail = ChildSummarySerializer(source="child", read_only=True)
    skill_detail = SkillSummarySerializer(source="skill", read_only=True)
    school_detail = SchoolSerializer(source="school", read_only=True)
    last_assessment_detail = AssessmentSerializer(source="last_assessment", read_only=True)

    class Meta:
        model = Progress
        fields = [
            "id",
            "child",
            "child_detail",
            "skill",
            "skill_detail",
            "school",
            "school_detail",
            "status",
            "current_score",
            "target_score",
            "attempts",
            "last_assessment",
            "last_assessment_detail",
            "evidence",
            "notes",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "child_detail",
            "skill_detail",
            "school_detail",
            "last_assessment_detail",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]


class MasteryRecordSerializer(COPPAConsentMixin, serializers.ModelSerializer):
    child_detail = ChildSummarySerializer(source="child", read_only=True)
    skill_detail = SkillSummarySerializer(source="skill", read_only=True)
    progress_detail = ProgressSerializer(source="progress", read_only=True)
    assessment_detail = AssessmentSerializer(source="assessment", read_only=True)
    mastered_by_detail = UserSummarySerializer(source="mastered_by", read_only=True)

    class Meta:
        model = MasteryRecord
        fields = [
            "id",
            "child",
            "child_detail",
            "skill",
            "skill_detail",
            "progress",
            "progress_detail",
            "assessment",
            "assessment_detail",
            "mastered_at",
            "mastered_by",
            "mastered_by_detail",
            "score",
            "evidence",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "child_detail",
            "skill_detail",
            "progress_detail",
            "assessment_detail",
            "mastered_by_detail",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        progress = attrs.get("progress") or getattr(self.instance, "progress", None)
        child = attrs.get("child") or getattr(self.instance, "child", None)
        skill = attrs.get("skill") or getattr(self.instance, "skill", None)
        if progress and child and progress.child_id != child.id:
            raise serializers.ValidationError({"progress": "Progress record must belong to the same child."})
        if progress and skill and progress.skill_id != skill.id:
            raise serializers.ValidationError({"progress": "Progress record must track the same skill."})
        return attrs
