from rest_framework import serializers

from apps.api.permissions import has_coppa_consent
from apps.api.serializers import SchoolSummarySerializer, UserSummarySerializer
from apps.schools.models import School
from apps.users.models import AuditLog, ChildProfile, ConsentLog, CustomUser, GuardianRelationship, Profile


class ProfileSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(),
        source="user",
        write_only=True,
        required=False,
    )

    class Meta:
        model = Profile
        fields = [
            "id",
            "user",
            "user_id",
            "display_name",
            "avatar",
            "timezone",
            "preferences",
            "onboarding_completed_at",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "is_deleted", "deleted_at", "created_at", "updated_at"]


class CustomUserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False)

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "username",
            "email",
            "password",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "phone_number",
            "metadata",
            "profile",
            "is_active",
            "is_staff",
            "is_superuser",
            "last_login",
            "date_joined",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "full_name",
            "profile",
            "is_staff",
            "is_superuser",
            "last_login",
            "date_joined",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {"metadata": {"write_only": True, "required": False}}

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = CustomUser(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class ChildProfileSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.filter(role=CustomUser.Role.STUDENT),
        source="user",
        write_only=True,
        required=False,
        allow_null=True,
    )
    school = SchoolSummarySerializer(read_only=True)
    school_id = serializers.PrimaryKeyRelatedField(
        queryset=School.objects.all(),
        source="school",
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = ChildProfile
        fields = [
            "id",
            "user",
            "user_id",
            "first_name",
            "last_name",
            "date_of_birth",
            "grade_level",
            "school",
            "school_id",
            "student_identifier",
            "learning_profile",
            "accommodations",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "school", "is_deleted", "deleted_at", "created_at", "updated_at"]
        extra_kwargs = {
            "date_of_birth": {"write_only": True},
            "student_identifier": {"write_only": True},
        }

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        child = instance
        sensitive_updates = {"learning_profile", "accommodations"} & set(attrs.keys())
        if child is not None and sensitive_updates and not has_coppa_consent(child):
            raise serializers.ValidationError("COPPA consent is required before updating child learning data.")
        return attrs


class GuardianRelationshipSerializer(serializers.ModelSerializer):
    guardian = UserSummarySerializer(read_only=True)
    guardian_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.filter(role=CustomUser.Role.GUARDIAN),
        source="guardian",
        write_only=True,
    )
    child = ChildProfileSerializer(read_only=True)
    child_id = serializers.PrimaryKeyRelatedField(queryset=ChildProfile.objects.all(), source="child", write_only=True)

    class Meta:
        model = GuardianRelationship
        fields = [
            "id",
            "guardian",
            "guardian_id",
            "child",
            "child_id",
            "relationship_type",
            "is_primary",
            "consent_status",
            "consent_expires_at",
            "permissions",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "guardian",
            "child",
            "consent_status",
            "consent_expires_at",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]


class ConsentLogSerializer(serializers.ModelSerializer):
    guardian_relationship = GuardianRelationshipSerializer(read_only=True)
    guardian_relationship_id = serializers.PrimaryKeyRelatedField(
        queryset=GuardianRelationship.objects.all(),
        source="guardian_relationship",
        write_only=True,
        required=False,
        allow_null=True,
    )
    guardian = UserSummarySerializer(read_only=True)
    child = ChildProfileSerializer(read_only=True)

    class Meta:
        model = ConsentLog
        fields = [
            "id",
            "guardian_relationship",
            "guardian_relationship_id",
            "guardian",
            "child",
            "consent_type",
            "status",
            "version",
            "source",
            "ip_address",
            "user_agent",
            "expires_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "guardian_relationship", "guardian", "child", "created_at", "updated_at"]
        extra_kwargs = {
            "ip_address": {"write_only": True, "required": False},
            "user_agent": {"write_only": True, "required": False},
        }

    def validate(self, attrs):
        relationship = attrs.get("guardian_relationship")
        if relationship:
            attrs.setdefault("guardian", relationship.guardian)
            attrs.setdefault("child", relationship.child)
        if not attrs.get("guardian") or not attrs.get("child"):
            raise serializers.ValidationError("Consent logs must be tied to a guardian relationship.")
        return attrs


class AuditLogSerializer(serializers.ModelSerializer):
    actor = UserSummarySerializer(read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor",
            "action",
            "entity_type",
            "entity_id",
            "before",
            "after",
            "metadata",
            "ip_address",
            "user_agent",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
