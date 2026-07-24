from rest_framework import serializers

from .models import Flag, Milestone, OutcomeAggregate, Prediction


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
