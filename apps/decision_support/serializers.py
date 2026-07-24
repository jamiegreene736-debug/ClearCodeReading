from rest_framework import serializers

from .models import (
    Flag,
    GrowthFlag,
    Milestone,
    MilestonePrediction,
    OutcomeAggregate,
    Prediction,
)


class ReadOnlyModelSerializer(serializers.ModelSerializer):
    def get_fields(self):
        fields = super().get_fields()
        for field in fields.values():
            field.read_only = True
        return fields


class FlagSerializer(ReadOnlyModelSerializer):
    class Meta:
        model = Flag
        fields = "__all__"


class PredictionSerializer(ReadOnlyModelSerializer):
    class Meta:
        model = Prediction
        fields = "__all__"


class MilestoneSerializer(ReadOnlyModelSerializer):
    class Meta:
        model = Milestone
        fields = "__all__"


class OutcomeAggregateSerializer(ReadOnlyModelSerializer):
    class Meta:
        model = OutcomeAggregate
        fields = "__all__"


class GrowthFlagSerializer(serializers.ModelSerializer):
    child_name = serializers.CharField(source="child.__str__", read_only=True)
    position_code = serializers.CharField(source="position.code", read_only=True)
    trigger_session_id = serializers.IntegerField(read_only=True)
    routed_to_details = serializers.SerializerMethodField()

    class Meta:
        model = GrowthFlag
        fields = [
            "id",
            "center",
            "child",
            "child_name",
            "trigger_session_id",
            "position",
            "position_code",
            "flag_code",
            "severity",
            "evidence_snapshot",
            "explanation",
            "advisory_recommendation",
            "status",
            "routed_to",
            "routed_to_details",
            "opened_at",
            "acknowledged_at",
            "acknowledged_by",
            "resolved_at",
            "resolved_by",
            "resolution_note",
            "revision",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_routed_to_details(self, obj) -> list[dict]:
        return [
            {
                "id": user.id,
                "name": user.get_full_name() or user.email,
                "email": user.email,
            }
            for user in obj.routed_to.all()
        ]


class MilestonePredictionSerializer(serializers.ModelSerializer):
    child_name = serializers.CharField(source="child.__str__", read_only=True)
    current_position = serializers.CharField(source="placement.current_position.code", read_only=True)
    target_position_code = serializers.CharField(source="target_position.code", read_only=True)
    confidence_band_sessions = serializers.SerializerMethodField()

    class Meta:
        model = MilestonePrediction
        fields = [
            "id",
            "center",
            "child",
            "child_name",
            "placement",
            "current_position",
            "target_position",
            "target_position_code",
            "target_label",
            "predicted_sessions",
            "predicted_date",
            "lower_bound_sessions",
            "upper_bound_sessions",
            "confidence_band_sessions",
            "confidence",
            "evidence_summary",
            "explanation",
            "parent_timeline",
            "disclaimer",
            "engine_version",
            "generated_at",
            "is_current",
            "revision",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_confidence_band_sessions(self, obj) -> dict:
        return {"lower": obj.lower_bound_sessions, "upper": obj.upper_bound_sessions}


class AcknowledgeFlagSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class ResolveFlagSerializer(serializers.Serializer):
    resolution_note = serializers.CharField(max_length=2000)


class EvaluateSessionSerializer(serializers.Serializer):
    session = serializers.IntegerField(min_value=1)


class GeneratePredictionSerializer(serializers.Serializer):
    child = serializers.IntegerField(min_value=1)
