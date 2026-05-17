from rest_framework import serializers

from apps.api.serializers import COPPAConsentMixin, ChildSummarySerializer, SkillSummarySerializer, UserSummarySerializer
from apps.assessments.models import Assessment
from apps.schools.serializers import SchoolSerializer


class AssessmentSerializer(COPPAConsentMixin, serializers.ModelSerializer):
    child_detail = ChildSummarySerializer(source="child", read_only=True)
    school_detail = SchoolSerializer(source="school", read_only=True)
    assigned_by_detail = UserSummarySerializer(source="assigned_by", read_only=True)
    skill_detail = SkillSummarySerializer(source="skill", read_only=True)
    percent_score = serializers.SerializerMethodField()

    class Meta:
        model = Assessment
        fields = [
            "id",
            "child",
            "child_detail",
            "school",
            "school_detail",
            "assigned_by",
            "assigned_by_detail",
            "assessment_type",
            "status",
            "title",
            "skill",
            "skill_detail",
            "scheduled_for",
            "started_at",
            "completed_at",
            "raw_score",
            "max_score",
            "percent_score",
            "percentile",
            "responses",
            "scoring",
            "recommendations",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "child_detail",
            "school_detail",
            "assigned_by_detail",
            "skill_detail",
            "percent_score",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]

    def get_percent_score(self, obj):
        if obj.raw_score is None or not obj.max_score:
            return None
        return round((obj.raw_score / obj.max_score) * 100, 2)
