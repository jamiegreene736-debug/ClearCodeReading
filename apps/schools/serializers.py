from rest_framework import serializers

from apps.api.serializers import UserSummarySerializer
from apps.schools.models import School, SchoolMembership
from apps.users.models import CustomUser


class SchoolMembershipSerializer(serializers.ModelSerializer):
    school_name = serializers.CharField(source="school.name", read_only=True)
    user = UserSummarySerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(queryset=CustomUser.objects.all(), source="user", write_only=True)
    invited_by = UserSummarySerializer(read_only=True)
    invited_by_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(),
        source="invited_by",
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = SchoolMembership
        fields = [
            "id",
            "school",
            "school_name",
            "user",
            "user_id",
            "role",
            "title",
            "permissions",
            "invited_by",
            "invited_by_id",
            "joined_at",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "school_name",
            "user",
            "invited_by",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]


class SchoolSerializer(serializers.ModelSerializer):
    memberships = SchoolMembershipSerializer(many=True, read_only=True)

    class Meta:
        model = School
        fields = [
            "id",
            "schema_name",
            "name",
            "slug",
            "district",
            "address",
            "contact_email",
            "contact_phone",
            "settings",
            "branding",
            "paid_until",
            "on_trial",
            "memberships",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "memberships", "is_deleted", "deleted_at", "created_at", "updated_at"]
        extra_kwargs = {
            "settings": {"write_only": True, "required": False},
        }
