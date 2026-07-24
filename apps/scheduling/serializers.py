from rest_framework import serializers

from apps.scheduling.models import ProviderAvailability, ScheduleBooking, ScheduleGroupProposal, WaitlistEntry


class ProviderAvailabilitySerializer(serializers.ModelSerializer):
    specialist_name = serializers.CharField(source="specialist.get_full_name", read_only=True)

    class Meta:
        model = ProviderAvailability
        fields = ["id", "center", "specialist", "specialist_name", "windows", "max_group_size", "is_active", "created_at", "updated_at"]

    def validate(self, attrs):
        specialist = attrs.get("specialist", getattr(self.instance, "specialist", None))
        center = attrs.get("center", getattr(self.instance, "center", None))
        if specialist and center and not specialist.school_memberships.filter(
            school=center,
            role__in=["owner", "admin", "specialist"],
            is_deleted=False,
        ).exists() and not specialist.is_superuser:
            raise serializers.ValidationError({"specialist": "Provider must belong to this center."})
        return attrs


class ScheduleBookingSerializer(serializers.ModelSerializer):
    child_name = serializers.CharField(source="child.__str__", read_only=True)
    specialist_name = serializers.CharField(source="specialist.get_full_name", read_only=True)
    idea_services_authorized = serializers.BooleanField(source="child.idea_services_authorized", read_only=True)

    class Meta:
        model = ScheduleBooking
        fields = "__all__"
        read_only_fields = [
            "proposal",
            "status",
            "approved_by",
            "approved_at",
            "external_booking_id",
            "sync_status",
            "sync_error",
            "sync_attempts",
            "last_sync_at",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        child = attrs.get("child", getattr(self.instance, "child", None))
        specialist = attrs.get("specialist", getattr(self.instance, "specialist", None))
        center = attrs.get("center", getattr(self.instance, "center", None))
        if child and not child.idea_services_authorized:
            raise serializers.ValidationError({"child": "IEP-aligned scheduling requires recorded parent consent and IEP-team approval."})
        if child and center and child.school_id != center.id:
            raise serializers.ValidationError({"center": "Booking must use the child's center."})
        if specialist and center and not specialist.school_memberships.filter(school=center, is_deleted=False).exists() and not specialist.is_superuser:
            raise serializers.ValidationError({"specialist": "Specialist must belong to the booking center."})
        ends_at = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        starts_at = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError({"ends_at": "End time must be after start time."})
        return attrs


class ScheduleGroupProposalSerializer(serializers.ModelSerializer):
    methodology = serializers.CharField(source="curriculum.code", read_only=True)
    specialist_name = serializers.CharField(source="specialist.get_full_name", read_only=True)
    students = serializers.SerializerMethodField()
    bookings = ScheduleBookingSerializer(many=True, read_only=True)
    approval_required = serializers.SerializerMethodField()

    class Meta:
        model = ScheduleGroupProposal
        fields = [
            "id",
            "center",
            "specialist",
            "specialist_name",
            "curriculum",
            "methodology",
            "starts_at",
            "ends_at",
            "score",
            "status",
            "rationale",
            "students",
            "bookings",
            "approval_required",
            "created_by",
            "reviewed_by",
            "reviewed_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_students(self, proposal):
        return [
            {
                "child": child.id,
                "display_name": str(child),
                "idea_services_authorized": child.idea_services_authorized,
                "iep_consent_indicator": "authorized" if child.idea_services_authorized else "pending",
            }
            for child in proposal.children.all()
        ]

    def get_approval_required(self, proposal):
        return proposal.status == ScheduleGroupProposal.Status.PROPOSED


class GenerateProposalsSerializer(serializers.Serializer):
    center = serializers.IntegerField(min_value=1)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    specialist = serializers.IntegerField(min_value=1, required=False)
    max_position_gap = serializers.IntegerField(min_value=0, max_value=5, default=1)
    session_minutes = serializers.IntegerField(min_value=30, max_value=180, default=60)
    limit = serializers.IntegerField(min_value=1, max_value=200, default=50)

    def validate(self, attrs):
        if attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError({"end_date": "Must be on or after start_date."})
        return attrs


class WaitlistEntrySerializer(serializers.ModelSerializer):
    child_name = serializers.CharField(source="child.__str__", read_only=True)

    class Meta:
        model = WaitlistEntry
        fields = "__all__"

    def validate(self, attrs):
        child = attrs.get("child", getattr(self.instance, "child", None))
        center = attrs.get("center", getattr(self.instance, "center", None))
        if child and center and child.school_id != center.id:
            raise serializers.ValidationError({"center": "Waitlist entry must use the child's center."})
        return attrs
