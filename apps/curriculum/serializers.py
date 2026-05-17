from rest_framework import serializers

from apps.curriculum.models import Lesson, Skill, TeachingAid


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
