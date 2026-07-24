from rest_framework import serializers

from apps.scheduling.models import ProviderAvailability, ScheduleBooking, WaitlistEntry


class ProviderAvailabilitySerializer(serializers.ModelSerializer):
    specialist_name = serializers.CharField(source="specialist.get_full_name", read_only=True)

    class Meta:
        model = ProviderAvailability
        fields = ["id", "center", "specialist", "specialist_name", "windows", "max_group_size", "is_active", "created_at", "updated_at"]


class ScheduleBookingSerializer(serializers.ModelSerializer):
    child_name = serializers.CharField(source="child.__str__", read_only=True)
    specialist_name = serializers.CharField(source="specialist.get_full_name", read_only=True)

    class Meta:
        model = ScheduleBooking
        fields = "__all__"
        read_only_fields = ["status", "approved_by", "approved_at", "external_booking_id", "sync_status", "created_at", "updated_at"]

    def validate(self, attrs):
        child = attrs.get("child", getattr(self.instance, "child", None))
        specialist = attrs.get("specialist", getattr(self.instance, "specialist", None))
        center = attrs.get("center", getattr(self.instance, "center", None))
        if child and not child.idea_services_authorized:
            raise serializers.ValidationError({"child": "IEP-aligned scheduling requires recorded parent consent and IEP-team approval."})
        if child and child.school_id and center and child.school_id != center.id:
            raise serializers.ValidationError({"center": "Booking must use the child's center."})
        if specialist and center and not specialist.school_memberships.filter(school=center, is_deleted=False).exists() and not specialist.is_superuser:
            raise serializers.ValidationError({"specialist": "Specialist must belong to the booking center."})
        if attrs.get("ends_at", getattr(self.instance, "ends_at", None)) <= attrs.get("starts_at", getattr(self.instance, "starts_at", None)):
            raise serializers.ValidationError({"ends_at": "End time must be after start time."})
        return attrs


class WaitlistEntrySerializer(serializers.ModelSerializer):
    child_name = serializers.CharField(source="child.__str__", read_only=True)

    class Meta:
        model = WaitlistEntry
        fields = "__all__"
