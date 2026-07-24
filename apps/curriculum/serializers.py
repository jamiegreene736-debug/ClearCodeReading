from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.curriculum.models import (
    ChildLessonAssignment,
    Curriculum,
    CurriculumSequence,
    Lesson,
    LessonTemplate,
    PlacementEvidence,
    PlacementRecommendation,
    RecommendedSequencePosition,
    SequencePlan,
    SequencePlanItem,
    Skill,
    SkillCrosswalk,
    StudentPlacement,
    StudentPlacementOverride,
    TeacherLessonTemplate,
    TeachingAid,
)


class SkillSerializer(serializers.ModelSerializer):
    prerequisites = serializers.PrimaryKeyRelatedField(queryset=Skill.objects.all(), many=True, required=False)
    prerequisite_details = serializers.SerializerMethodField()

    class Meta:
        model = Skill
        fields = [
            "id",
            "code",
            "name",
            "domain",
            "grade_band",
            "description",
            "prerequisites",
            "prerequisite_details",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "prerequisite_details", "is_deleted", "deleted_at", "created_at", "updated_at"]

    def get_prerequisite_details(self, obj):
        return [{"id": skill.id, "code": skill.code, "name": skill.name} for skill in obj.prerequisites.all()]


class TeachingAidSerializer(serializers.ModelSerializer):
    lesson_title = serializers.CharField(source="lesson.title", read_only=True)
    skill_code = serializers.CharField(source="skill.code", read_only=True)

    class Meta:
        model = TeachingAid
        fields = [
            "id",
            "lesson",
            "lesson_title",
            "skill",
            "skill_code",
            "title",
            "aid_type",
            "file",
            "url",
            "content",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "lesson_title", "skill_code", "is_deleted", "deleted_at", "created_at", "updated_at"]

    def validate(self, attrs):
        if not attrs.get("lesson") and not attrs.get("skill"):
            raise serializers.ValidationError("A teaching aid must be attached to a lesson or skill.")
        return attrs


class LessonSerializer(serializers.ModelSerializer):
    skill_detail = SkillSerializer(source="skill", read_only=True)
    teaching_aids = TeachingAidSerializer(many=True, read_only=True)

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "slug",
            "skill",
            "skill_detail",
            "grade_level",
            "duration_minutes",
            "objective",
            "content",
            "materials",
            "differentiation",
            "is_published",
            "teaching_aids",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "skill_detail", "teaching_aids", "is_deleted", "deleted_at", "created_at", "updated_at"]


class LessonTemplateSerializer(serializers.ModelSerializer):
    skill_detail = SkillSerializer(source="skill", read_only=True)

    class Meta:
        model = LessonTemplate
        fields = [
            "id",
            "title",
            "slug",
            "skill",
            "skill_detail",
            "grade_band",
            "description",
            "goal",
            "recommended_minutes",
            "activities",
            "materials",
            "is_active",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "skill_detail", "is_deleted", "deleted_at", "created_at", "updated_at"]


class TeacherLessonTemplateSerializer(serializers.ModelSerializer):
    teacher_name = serializers.CharField(source="teacher.get_full_name", read_only=True)
    teacher_email = serializers.EmailField(source="teacher.email", read_only=True)
    template_detail = LessonTemplateSerializer(source="template", read_only=True)
    assigned_by_name = serializers.CharField(source="assigned_by.get_full_name", read_only=True)

    class Meta:
        model = TeacherLessonTemplate
        fields = [
            "id",
            "teacher",
            "teacher_name",
            "teacher_email",
            "template",
            "template_detail",
            "assigned_by",
            "assigned_by_name",
            "notes",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "teacher_name",
            "teacher_email",
            "template_detail",
            "assigned_by_name",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]


class ChildLessonAssignmentSerializer(serializers.ModelSerializer):
    child_name = serializers.CharField(source="child.__str__", read_only=True)
    template_detail = LessonTemplateSerializer(source="template", read_only=True)
    assigned_by_name = serializers.CharField(source="assigned_by.get_full_name", read_only=True)

    class Meta:
        model = ChildLessonAssignment
        fields = [
            "id",
            "child",
            "child_name",
            "template",
            "template_detail",
            "assigned_by",
            "assigned_by_name",
            "status",
            "due_date",
            "teacher_notes",
            "completed_at",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "child_name",
            "template_detail",
            "assigned_by_name",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]


class CurriculumSerializer(serializers.ModelSerializer):
    class Meta:
        model = Curriculum
        fields = ["id", "center", "code", "name", "version", "is_active", "metadata"]
        read_only_fields = fields


class CurriculumSequenceSerializer(serializers.ModelSerializer):
    prerequisite_codes = serializers.SerializerMethodField()

    class Meta:
        model = CurriculumSequence
        fields = [
            "id",
            "center",
            "curriculum",
            "code",
            "sequence_order",
            "level",
            "lesson_number",
            "concept_number",
            "title",
            "position_type",
            "description",
            "letter_sounds",
            "word_types",
            "syllable_types",
            "high_frequency_words",
            "red_words_spell_and_read",
            "red_words_read_only",
            "activities",
            "item_set_schema",
            "mastery_criteria",
            "prerequisite_codes",
        ]
        read_only_fields = fields

    def get_prerequisite_codes(self, obj) -> list[str]:
        return list(obj.prerequisites.order_by("sequence_order").values_list("code", flat=True))


class SkillCrosswalkSerializer(serializers.ModelSerializer):
    skill_node_a_detail = CurriculumSequenceSerializer(source="skill_node_a", read_only=True)
    skill_node_b_detail = CurriculumSequenceSerializer(source="skill_node_b", read_only=True)
    scope = serializers.SerializerMethodField()

    class Meta:
        model = SkillCrosswalk
        fields = [
            "id",
            "center",
            "scope",
            "skill_node_a",
            "skill_node_a_detail",
            "skill_node_b",
            "skill_node_b_detail",
            "mapping_type",
            "equivalence",
            "notes",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_scope(self, obj) -> str:
        return "center" if obj.center_id else "global"


class StudentPlacementOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentPlacementOverride
        fields = [
            "id",
            "previous_position",
            "new_position",
            "rationale",
            "evidence_considered",
            "source_recommendation",
            "specialist",
            "overridden_at",
        ]
        read_only_fields = fields


class StudentPlacementSerializer(serializers.ModelSerializer):
    current_position_detail = CurriculumSequenceSerializer(source="current_position", read_only=True)
    override_history = StudentPlacementOverrideSerializer(many=True, read_only=True)

    class Meta:
        model = StudentPlacement
        fields = [
            "id",
            "center",
            "child",
            "curriculum",
            "current_position",
            "current_position_detail",
            "methodology_rationale",
            "placement_evidence",
            "placed_at",
            "placed_by",
            "is_active",
            "override_history",
        ]
        read_only_fields = fields


class PlacementEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlacementEvidence
        fields = [
            "id",
            "center",
            "child",
            "curriculum",
            "source_assessment",
            "instrument",
            "source",
            "status",
            "assessment_version",
            "administered_by",
            "administered_at",
            "instructional_grade_band",
            "raw_results",
            "supporting_context",
            "revision",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "center", "administered_by", "revision", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = self.instance or PlacementEvidence()
        for name, value in attrs.items():
            setattr(instance, name, value)
        child = attrs.get("child", getattr(instance, "child", None))
        curriculum = attrs.get("curriculum", getattr(instance, "curriculum", None))
        if child and curriculum and child.school_id and child.school_id != curriculum.center_id:
            raise serializers.ValidationError("Child and curriculum must belong to the same center.")
        return attrs

    def _clean(self, instance):
        try:
            instance.full_clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error

    def create(self, validated_data):
        request = self.context["request"]
        child = validated_data["child"]
        curriculum = validated_data["curriculum"]
        instance = PlacementEvidence(
            **validated_data,
            center=child.school or curriculum.center,
            administered_by=request.user,
            created_by=request.user,
            updated_by=request.user,
        )
        self._clean(instance)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        for name, value in validated_data.items():
            setattr(instance, name, value)
        instance.updated_by = self.context["request"].user
        self._clean(instance)
        instance.save()
        return instance


class RecommendedSequencePositionSerializer(serializers.ModelSerializer):
    position = CurriculumSequenceSerializer(read_only=True)

    class Meta:
        model = RecommendedSequencePosition
        fields = ["priority", "position", "gap_codes", "rationale"]
        read_only_fields = fields


class SequencePlanItemSerializer(serializers.ModelSerializer):
    position_detail = CurriculumSequenceSerializer(source="position", read_only=True)

    class Meta:
        model = SequencePlanItem
        fields = ["id", "position", "position_detail", "order", "status", "notes", "created_at", "updated_at"]
        read_only_fields = fields


class SequencePlanSerializer(serializers.ModelSerializer):
    items = SequencePlanItemSerializer(many=True, read_only=True)

    class Meta:
        model = SequencePlan
        fields = [
            "id",
            "center",
            "placement",
            "status",
            "created_from_recommendation",
            "specialist_notes",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class UpdateSequencePlanItemSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=SequencePlanItem.Status.choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide a status or specialist note to update.")
        return attrs


class PlacementRecommendationSerializer(serializers.ModelSerializer):
    evidence = PlacementEvidenceSerializer(read_only=True)
    recommended_position_detail = CurriculumSequenceSerializer(source="recommended_position", read_only=True)
    final_position_detail = CurriculumSequenceSerializer(source="final_position", read_only=True)
    recommended_sequence = RecommendedSequencePositionSerializer(many=True, read_only=True)
    resulting_placement = StudentPlacementSerializer(read_only=True)
    materialized_sequence_plan = SequencePlanSerializer(read_only=True)

    class Meta:
        model = PlacementRecommendation
        fields = [
            "id",
            "center",
            "evidence",
            "recommended_curriculum",
            "recommended_position",
            "recommended_position_detail",
            "decision",
            "status",
            "deficit_profile",
            "rule_trace",
            "rationale",
            "advisory_narrative",
            "ai_metadata",
            "recommended_sequence",
            "final_position",
            "final_curriculum",
            "final_position_detail",
            "override_rationale",
            "evidence_considered",
            "confirmed_by",
            "confirmed_at",
            "resulting_placement",
            "materialized_sequence_plan",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ConfirmPlacementRecommendationSerializer(serializers.Serializer):
    final_position = serializers.PrimaryKeyRelatedField(
        queryset=CurriculumSequence.objects.filter(is_deleted=False),
        required=False,
        allow_null=True,
    )
    override_rationale = serializers.CharField(required=False, allow_blank=True)
    evidence_considered = serializers.DictField(required=False)
    create_sequence_plan = serializers.BooleanField(required=False, default=True)
