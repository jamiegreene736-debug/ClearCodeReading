from rest_framework import serializers

from apps.api.serializers import COPPAConsentMixin, ChildSummarySerializer, SkillSummarySerializer, UserSummarySerializer
from apps.assessments.models import Assessment, AssessmentQuestion, AssessmentResult, ChildAssessmentResponse, QuestionOption
from apps.schools.models import School
from apps.schools.serializers import SchoolSerializer
from apps.users.models import ChildProfile


class QuestionOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionOption
        fields = ["id", "label", "value", "sort_order"]
        read_only_fields = fields


class AssessmentQuestionSerializer(serializers.ModelSerializer):
    options = serializers.SerializerMethodField()

    class Meta:
        model = AssessmentQuestion
        fields = [
            "id",
            "category",
            "difficulty",
            "question_type",
            "question_text",
            "audio_file",
            "image_url",
            "options",
            "sort_order",
        ]
        read_only_fields = fields

    def get_options(self, obj):
        options = obj.question_options.filter(is_deleted=False).order_by("sort_order", "id")
        if options.exists():
            return QuestionOptionSerializer(options, many=True).data
        return obj.options


class AssessmentResultSerializer(serializers.ModelSerializer):
    final_message = serializers.SerializerMethodField()

    class Meta:
        model = AssessmentResult
        fields = [
            "id",
            "assessment",
            "final_scores",
            "reading_age",
            "grade_equivalent",
            "category_breakdown",
            "strengths",
            "growth_areas",
            "teacher_summary",
            "final_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_final_message(self, obj):
        return obj.final_scores.get("final_message", f"You are reading at an {obj.reading_age}-year-old level")


class StartSurveySerializer(serializers.Serializer):
    child = serializers.PrimaryKeyRelatedField(queryset=ChildProfile.objects.filter(is_deleted=False))
    school = serializers.PrimaryKeyRelatedField(queryset=School.objects.filter(is_deleted=False), required=False, allow_null=True)
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    first_section = serializers.ChoiceField(
        choices=AssessmentQuestion.Category.choices,
        required=False,
        default=AssessmentQuestion.Category.PHONEMIC_AWARENESS,
    )
    question_limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=5)


class ChildAssessmentAnswerSerializer(serializers.Serializer):
    question = serializers.PrimaryKeyRelatedField(queryset=AssessmentQuestion.objects.filter(is_active=True, is_deleted=False))
    selected_option = serializers.PrimaryKeyRelatedField(
        queryset=QuestionOption.objects.filter(is_deleted=False),
        required=False,
        allow_null=True,
    )
    answer = serializers.JSONField(required=False, default=dict)
    time_taken = serializers.IntegerField(required=False, allow_null=True, min_value=0)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        selected_option = attrs.get("selected_option")
        question = attrs["question"]
        if selected_option is not None and selected_option.question_id != question.id:
            raise serializers.ValidationError({"selected_option": "Selected option does not belong to this question."})
        if selected_option is None and not attrs.get("answer"):
            raise serializers.ValidationError("Provide either selected_option or answer.")
        return attrs


class AnswerSubmissionSerializer(serializers.Serializer):
    answers = ChildAssessmentAnswerSerializer(many=True, required=False)
    question = serializers.PrimaryKeyRelatedField(
        queryset=AssessmentQuestion.objects.filter(is_active=True, is_deleted=False),
        required=False,
    )
    selected_option = serializers.PrimaryKeyRelatedField(
        queryset=QuestionOption.objects.filter(is_deleted=False),
        required=False,
        allow_null=True,
    )
    answer = serializers.JSONField(required=False, default=dict)
    time_taken = serializers.IntegerField(required=False, allow_null=True, min_value=0)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get("answers"):
            return attrs
        if not attrs.get("question"):
            raise serializers.ValidationError({"answers": "Provide answers or a single question answer payload."})

        nested = ChildAssessmentAnswerSerializer(data={
            "question": attrs["question"].id,
            "selected_option": attrs["selected_option"].id if attrs.get("selected_option") else None,
            "answer": attrs.get("answer", {}),
            "time_taken": attrs.get("time_taken"),
        })
        nested.is_valid(raise_exception=True)
        attrs["answers"] = [nested.validated_data]
        return attrs


class ChildAssessmentResponseSerializer(serializers.ModelSerializer):
    question_detail = AssessmentQuestionSerializer(source="question", read_only=True)
    selected_option_detail = QuestionOptionSerializer(source="selected_option", read_only=True)

    class Meta:
        model = ChildAssessmentResponse
        fields = [
            "id",
            "assessment",
            "child",
            "question",
            "question_detail",
            "selected_option",
            "selected_option_detail",
            "answer",
            "is_correct",
            "score_value",
            "time_taken",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AssessmentSerializer(COPPAConsentMixin, serializers.ModelSerializer):
    child_detail = ChildSummarySerializer(source="child", read_only=True)
    school_detail = SchoolSerializer(source="school", read_only=True)
    assigned_by_detail = UserSummarySerializer(source="assigned_by", read_only=True)
    skill_detail = SkillSummarySerializer(source="skill", read_only=True)
    percent_score = serializers.SerializerMethodField()
    result = AssessmentResultSerializer(read_only=True)

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
            "survey_completed_at",
            "completed_at",
            "raw_score",
            "max_score",
            "overall_score",
            "reading_age",
            "percent_score",
            "percentile",
            "responses",
            "scoring",
            "recommendations",
            "result",
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
            "survey_completed_at",
            "overall_score",
            "reading_age",
            "percent_score",
            "result",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]

    def get_percent_score(self, obj):
        if obj.raw_score is None or not obj.max_score:
            return None
        return round((obj.raw_score / obj.max_score) * 100, 2)


class SurveyStartResponseSerializer(serializers.Serializer):
    assessment = AssessmentSerializer()
    questions = AssessmentQuestionSerializer(many=True)
    section = serializers.CharField()


class SurveyProgressResponseSerializer(serializers.Serializer):
    assessment = AssessmentSerializer()
    responses = ChildAssessmentResponseSerializer(many=True)
    answered_count = serializers.IntegerField()


class SurveyFinalReportSerializer(serializers.Serializer):
    assessment = AssessmentSerializer()
    result = AssessmentResultSerializer()
    final_message = serializers.CharField()
