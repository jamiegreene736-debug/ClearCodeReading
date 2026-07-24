from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from apps.scheduling.models import (
    Group,
    GroupMembership,
    ProviderAvailability,
    ScheduleBooking,
    ScheduleGroupProposal,
    WaitlistEntry,
)
from apps.users.models import ChildProfile


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


class GroupSerializer(serializers.ModelSerializer):
    students = serializers.PrimaryKeyRelatedField(
        queryset=ChildProfile.objects.filter(is_deleted=False),
        many=True,
        required=False,
    )
    student_details = serializers.SerializerMethodField()
    methodology = serializers.CharField(source="curriculum.code", read_only=True)
    primary_specialist_name = serializers.CharField(source="primary_specialist.get_full_name", read_only=True)

    class Meta:
        model = Group
        fields = [
            "id",
            "center",
            "name",
            "curriculum",
            "methodology",
            "skill_band",
            "sequence_start",
            "sequence_end",
            "students",
            "student_details",
            "primary_specialist",
            "primary_specialist_name",
            "is_active",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "methodology", "student_details", "primary_specialist_name", "created_at", "updated_at"]

    def get_student_details(self, obj):
        return [
            {"id": child.id, "display_name": str(child)}
            for child in obj.students.filter(is_deleted=False).order_by("last_name", "first_name")
        ]

    def validate_students(self, students):
        student_ids = [student.id for student in students]
        if len(student_ids) != len(set(student_ids)):
            raise serializers.ValidationError("A student can appear in a group only once.")
        return students

    @staticmethod
    def _full_clean(instance):
        try:
            instance.full_clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error

    @classmethod
    def _sync_students(cls, group, students):
        requested_ids = {student.id for student in students}
        group.memberships.exclude(child_id__in=requested_ids).delete()
        existing_ids = set(group.memberships.values_list("child_id", flat=True))
        for child in students:
            if child.id in existing_ids:
                continue
            membership = GroupMembership(group=group, child=child)
            cls._full_clean(membership)
            membership.save()

    @transaction.atomic
    def create(self, validated_data):
        students = validated_data.pop("students", [])
        group = Group(**validated_data)
        self._full_clean(group)
        group.save()
        self._sync_students(group, students)
        return group

    @transaction.atomic
    def update(self, instance, validated_data):
        students = validated_data.pop("students", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        self._full_clean(instance)
        instance.save()
        if students is not None:
            self._sync_students(instance, students)
        else:
            for membership in instance.memberships.select_related(
                "child",
                "child__school",
                "group__curriculum",
                "group__sequence_start",
                "group__sequence_end",
            ):
                self._full_clean(membership)
        return instance


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
