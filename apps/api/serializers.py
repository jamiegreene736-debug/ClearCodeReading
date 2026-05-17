from rest_framework import serializers

from apps.api.permissions import CONSENT_REQUIRED_TYPES, has_coppa_consent
from apps.curriculum.models import Skill
from apps.schools.models import School
from apps.users.models import ChildProfile, CustomUser


class COPPAConsentMixin:
    coppa_child_field = "child"
    coppa_consent_types = CONSENT_REQUIRED_TYPES

    def _get_child_for_coppa(self, attrs):
        child = attrs.get(self.coppa_child_field)
        if child is not None:
            return child
        instance = getattr(self, "instance", None)
        if instance is not None:
            return getattr(instance, self.coppa_child_field, None)
        return None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        child = self._get_child_for_coppa(attrs)
        if child is not None and not has_coppa_consent(child, self.coppa_consent_types):
            raise serializers.ValidationError(
                {
                    self.coppa_child_field: (
                        "COPPA consent is required before creating or updating "
                        "assessment, progress, or child learning records."
                    )
                }
            )
        return attrs


class UserSummarySerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = CustomUser
        fields = ["id", "email", "first_name", "last_name", "full_name", "role"]
        read_only_fields = fields


class SchoolSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = School
        fields = ["id", "name", "slug", "district"]
        read_only_fields = fields


class ChildSummarySerializer(serializers.ModelSerializer):
    school = SchoolSummarySerializer(read_only=True)

    class Meta:
        model = ChildProfile
        fields = ["id", "first_name", "last_name", "grade_level", "school"]
        read_only_fields = fields


class SkillSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ["id", "code", "name", "domain", "grade_band"]
        read_only_fields = fields
