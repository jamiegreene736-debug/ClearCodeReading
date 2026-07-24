from rest_framework import serializers

from apps.outcomes.models import DeIdentifiedOutcomeSnapshot


class DeIdentifiedOutcomeSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeIdentifiedOutcomeSnapshot
        fields = [
            "id",
            "center_key",
            "methodology",
            "grade_band",
            "window_type",
            "window_start",
            "window_end",
            "metric_scope",
            "aggregate_version",
            "privacy_floor",
            "metrics",
            "source_counts",
            "generated_at",
        ]
        read_only_fields = fields
