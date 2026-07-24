from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.api.permissions import has_coppa_consent
from apps.api.serializers import SchoolSummarySerializer, UserSummarySerializer
from apps.schools.models import School
from apps.users.models import (
    AuditLog,
    ChildProfile,
    ConsentLog,
    ConsentRecord,
    CustomUser,
    GuardianRelationship,
    Profile,
)


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
            "availability_windows",
            "iep_status",
            "idea_parent_consent_status",
            "idea_parent_consented_at",
            "iep_team_approval_status",
            "iep_team_approved_at",
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
        sensitive_updates = {
            "learning_profile",
            "accommodations",
            "availability_windows",
            "iep_status",
            "idea_parent_consent_status",
            "iep_team_approval_status",
        } & set(attrs.keys())
        if child is not None and sensitive_updates and not has_coppa_consent(child):
            raise serializers.ValidationError("COPPA consent is required before updating child learning data.")
        windows = attrs.get("availability_windows")
        if windows is not None:
            required = {"day_of_week", "start_time", "end_time", "timezone"}
            if not isinstance(windows, list) or any(
                not isinstance(window, dict) or not required.issubset(window)
                for window in windows
            ):
                raise serializers.ValidationError(
                    {"availability_windows": "Each window requires day_of_week, start_time, end_time, and timezone."}
                )
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


class ConsentRecordSerializer(serializers.ModelSerializer):
    child_id = serializers.PrimaryKeyRelatedField(
        queryset=ChildProfile.objects.filter(is_deleted=False),
        source="child",
    )
    center_id = serializers.IntegerField(read_only=True)
    granted_by_id = serializers.IntegerField(read_only=True)
    created_by_id = serializers.IntegerField(read_only=True)
    is_effective = serializers.BooleanField(read_only=True)

    class Meta:
        model = ConsentRecord
        fields = [
            "id",
            "child_id",
            "center_id",
            "consent_type",
            "status",
            "version",
            "granted_by_id",
            "granted_at",
            "expires_at",
            "evidence_notes",
            "source_document_ref",
            "created_by_id",
            "is_effective",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "center_id",
            "version",
            "granted_by_id",
            "granted_at",
            "created_by_id",
            "is_effective",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        child = attrs["child"]
        if child.school_id is None:
            raise serializers.ValidationError({"child_id": "The child must belong to a center."})
        request = self.context["request"]
        from apps.api.permissions import has_school_membership
        from apps.schools.models import SchoolMembership

        if not has_school_membership(
            request.user,
            child.school,
            roles=[SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN],
        ):
            raise PermissionDenied("You cannot manage consent for this center.")
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        child = validated_data["child"]
        validated_data["center"] = child.school
        validated_data["created_by"] = request.user
        if validated_data["status"] == ConsentRecord.Status.GRANTED:
            validated_data["granted_by"] = request.user
        return super().create(validated_data)


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
