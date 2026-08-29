from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.workforce.models import (
    ClassificationReview,
    Engagement,
    PayableItem,
    Payment,
    PaymentRun,
    ProviderOnboarding,
    RateSchedule,
    WorkerAssignment,
    WorkerProfile,
)
from apps.workforce.services import ensure_florida_reporting_task, payment_readiness


def full_clean_or_error(instance) -> None:
    try:
        instance.full_clean()
    except DjangoValidationError as error:
        detail = error.message_dict if hasattr(error, "message_dict") else error.messages
        raise serializers.ValidationError(detail) from error


class WorkerProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    display_name = serializers.CharField(source="user.get_full_name", read_only=True)

    class Meta:
        model = WorkerProfile
        fields = ["id", "user", "email", "display_name", "status", "created_at", "updated_at"]
        read_only_fields = ["id", "email", "display_name", "created_at", "updated_at"]


class WorkerAssignmentSerializer(serializers.ModelSerializer):
    center_name = serializers.CharField(source="center.name", read_only=True)

    class Meta:
        model = WorkerAssignment
        fields = ["id", "center", "center_name", "is_active", "starts_on", "ends_on", "created_at", "updated_at"]
        read_only_fields = ["id", "center_name", "created_at", "updated_at"]


class EngagementSerializer(serializers.ModelSerializer):
    worker_email = serializers.EmailField(source="worker.user.email", read_only=True)
    worker_name = serializers.CharField(source="worker.user.get_full_name", read_only=True)
    assignments = WorkerAssignmentSerializer(many=True, read_only=True)
    payment_ready = serializers.SerializerMethodField()
    payment_blockers = serializers.SerializerMethodField()

    class Meta:
        model = Engagement
        fields = [
            "id",
            "payer",
            "worker",
            "worker_email",
            "worker_name",
            "classification",
            "status",
            "work_state",
            "delivery_context",
            "starts_on",
            "ends_on",
            "contract_signed_on",
            "first_reportable_payment_on",
            "anticipated_calendar_year_compensation",
            "assignments",
            "payment_ready",
            "payment_blockers",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "worker_email",
            "worker_name",
            "classification",
            "status",
            "assignments",
            "payment_ready",
            "payment_blockers",
            "created_at",
            "updated_at",
        ]

    def get_payment_ready(self, obj) -> bool:
        return payment_readiness(obj).ready

    def get_payment_blockers(self, obj) -> tuple[str, ...]:
        return payment_readiness(obj).blockers

    def create(self, validated_data):
        instance = Engagement(**validated_data)
        full_clean_or_error(instance)
        instance.save()
        ensure_florida_reporting_task(instance)
        return instance

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        full_clean_or_error(instance)
        instance.save()
        ensure_florida_reporting_task(instance)
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request is None:
            return data
        user = request.user
        is_worker = instance.worker.user_id == user.id
        is_central = user.is_superuser or user.workforce_roles.filter(
            payer=instance.payer,
            is_active=True,
        ).exists()
        if not is_worker and not is_central:
            data.pop("anticipated_calendar_year_compensation", None)
        return data


class AssignmentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkerAssignment
        fields = ["center", "starts_on", "ends_on"]


class ClassificationReviewSerializer(serializers.ModelSerializer):
    reviewer_email = serializers.EmailField(source="reviewed_by.email", read_only=True)

    class Meta:
        model = ClassificationReview
        fields = [
            "id",
            "engagement",
            "version",
            "decision",
            "rationale",
            "evidence",
            "reviewed_by",
            "reviewer_email",
            "reviewed_at",
            "next_review_due",
            "created_at",
        ]
        read_only_fields = fields


class ClassificationReviewInputSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=ClassificationReview.Decision.choices)
    rationale = serializers.CharField(trim_whitespace=True, max_length=10000)
    evidence = serializers.JSONField(default=dict)
    next_review_due = serializers.DateField(required=False, allow_null=True)


class ProviderOnboardingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProviderOnboarding
        fields = [
            "id",
            "engagement",
            "provider",
            "status",
            "invite_expires_at",
            "last_synced_at",
            "remediation_codes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class RateScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = RateSchedule
        fields = [
            "id",
            "engagement",
            "center",
            "unit",
            "amount",
            "currency",
            "starts_on",
            "ends_on",
            "status",
            "created_by",
            "approved_by",
            "approved_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "created_by", "approved_by", "approved_at", "created_at", "updated_at"]

    def create(self, validated_data):
        instance = RateSchedule(created_by=self.context["request"].user, **validated_data)
        full_clean_or_error(instance)
        instance.save()
        return instance


class PayableItemSerializer(serializers.ModelSerializer):
    worker_email = serializers.EmailField(source="engagement.worker.user.email", read_only=True)

    class Meta:
        model = PayableItem
        fields = [
            "id",
            "engagement",
            "worker_email",
            "center",
            "source_session",
            "service_date",
            "description",
            "units",
            "rate",
            "gross_amount",
            "status",
            "created_by",
            "approved_by",
            "approved_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SessionPayableInputSerializer(serializers.Serializer):
    session = serializers.IntegerField(min_value=1)


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id",
            "payable",
            "engagement",
            "amount",
            "status",
            "external_payment_id",
            "failure_code",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PaymentRunSerializer(serializers.ModelSerializer):
    payments = PaymentSerializer(many=True, read_only=True)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PaymentRun
        fields = [
            "id",
            "payer",
            "period_start",
            "period_end",
            "idempotency_key",
            "status",
            "created_by",
            "reviewed_by",
            "reviewed_at",
            "approved_by",
            "approved_at",
            "external_batch_id",
            "total_amount",
            "payments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "idempotency_key",
            "status",
            "created_by",
            "reviewed_by",
            "reviewed_at",
            "approved_by",
            "approved_at",
            "external_batch_id",
            "total_amount",
            "payments",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        instance = PaymentRun(created_by=self.context["request"].user, **validated_data)
        full_clean_or_error(instance)
        instance.save()
        return instance


class PaymentRunPayablesSerializer(serializers.Serializer):
    payables = serializers.PrimaryKeyRelatedField(queryset=PayableItem.objects.all(), many=True, allow_empty=False)


class ProviderWebhookResponseSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    duplicate = serializers.BooleanField()
    event_id = serializers.CharField()
