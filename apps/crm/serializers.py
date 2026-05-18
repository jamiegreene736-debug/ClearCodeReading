from rest_framework import serializers

from apps.api.serializers import SchoolSummarySerializer, UserSummarySerializer
from apps.crm.models import Lead, Opportunity


class LeadSerializer(serializers.ModelSerializer):
    assigned_to_detail = UserSummarySerializer(source="assigned_to", read_only=True)

    class Meta:
        model = Lead
        fields = [
            "id",
            "school_name",
            "contact_name",
            "contact_email",
            "contact_phone",
            "audience",
            "organization_name",
            "source",
            "status",
            "assigned_to",
            "assigned_to_detail",
            "linked_user",
            "estimated_students",
            "notes",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "assigned_to_detail", "is_deleted", "deleted_at", "created_at", "updated_at"]
        extra_kwargs = {
            "contact_email": {"write_only": True},
            "contact_phone": {"write_only": True, "required": False},
        }


class OpportunitySerializer(serializers.ModelSerializer):
    lead_detail = LeadSerializer(source="lead", read_only=True)
    school_detail = SchoolSummarySerializer(source="school", read_only=True)
    owner_detail = UserSummarySerializer(source="owner", read_only=True)

    class Meta:
        model = Opportunity
        fields = [
            "id",
            "lead",
            "lead_detail",
            "school",
            "school_detail",
            "owner",
            "owner_detail",
            "name",
            "stage",
            "value",
            "probability",
            "expected_close_date",
            "closed_at",
            "lost_reason",
            "next_steps",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "lead_detail",
            "school_detail",
            "owner_detail",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]

    def validate_probability(self, value):
        if value > 100:
            raise serializers.ValidationError("Probability must be between 0 and 100.")
        return value
