from rest_framework import serializers

from apps.api.serializers import SchoolSummarySerializer, UserSummarySerializer
from apps.crm.models import Company, Lead, Opportunity


class CompanySerializer(serializers.ModelSerializer):
    owner_detail = UserSummarySerializer(source="owner", read_only=True)

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "website",
            "owner",
            "owner_detail",
            "notes",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "owner_detail", "is_deleted", "deleted_at", "created_at", "updated_at"]


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
            "company",
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
    company_detail = CompanySerializer(source="company", read_only=True)
    school_detail = SchoolSummarySerializer(source="school", read_only=True)
    owner_detail = UserSummarySerializer(source="owner", read_only=True)
    pipeline_display = serializers.CharField(source="get_pipeline_display", read_only=True)
    stage_display = serializers.CharField(source="get_stage_display", read_only=True)

    class Meta:
        model = Opportunity
        fields = [
            "id",
            "lead",
            "lead_detail",
            "company",
            "company_detail",
            "school",
            "school_detail",
            "owner",
            "owner_detail",
            "name",
            "pipeline",
            "pipeline_display",
            "stage",
            "stage_display",
            "value",
            "probability",
            "expected_close_date",
            "closed_at",
            "lost_reason",
            "next_steps",
            "metadata",
            "related_deals",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "lead_detail",
            "company_detail",
            "school_detail",
            "owner_detail",
            "pipeline_display",
            "stage_display",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]

    def validate_probability(self, value):
        if value > 100:
            raise serializers.ValidationError("Probability must be between 0 and 100.")
        return value

    def validate(self, attrs):
        pipeline = attrs.get("pipeline", getattr(self.instance, "pipeline", Opportunity.Pipeline.FAMILY_ENROLLMENT))
        stage = attrs.get("stage")
        if stage is None:
            if self.instance is None or "pipeline" in attrs:
                stage = Opportunity.initial_stage_for_pipeline(pipeline)
                attrs["stage"] = stage
            else:
                stage = self.instance.stage
        if stage not in Opportunity.stage_values_for_pipeline(pipeline):
            raise serializers.ValidationError({"stage": "Choose a stage in this deal's pipeline."})

        lead = attrs.get("lead", getattr(self.instance, "lead", None))
        company = attrs.get("company", getattr(self.instance, "company", None))
        school = attrs.get("school", getattr(self.instance, "school", None))
        if not lead and not company and not school:
            raise serializers.ValidationError("Associate the deal with a contact or company.")
        if lead and company and lead.company_id not in {None, company.pk}:
            raise serializers.ValidationError({"company": "The deal company must match the contact's company."})
        return attrs
