from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from apps.api.permissions import user_can_evaluate_child
from apps.curriculum.models import CurriculumSequence, StudentPlacement
from apps.sessions.models import Session, SessionRevision


class SessionRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionRevision
        fields = ["id", "revision", "changed_by", "snapshot", "created_at"]
        read_only_fields = fields


class SessionSerializer(serializers.ModelSerializer):
    curriculum_position = serializers.PrimaryKeyRelatedField(
        queryset=CurriculumSequence.objects.filter(is_deleted=False),
        required=False,
    )
    targeted_positions = serializers.PrimaryKeyRelatedField(
        queryset=CurriculumSequence.objects.filter(is_deleted=False),
        many=True,
        required=False,
    )
    specialist_name = serializers.CharField(source="specialist.get_full_name", read_only=True)
    child_name = serializers.CharField(source="child.__str__", read_only=True)
    position_code = serializers.CharField(source="curriculum_position.code", read_only=True)
    revision_history = SessionRevisionSerializer(many=True, read_only=True)

    class Meta:
        model = Session
        fields = [
            "id",
            "center",
            "child",
            "child_name",
            "specialist",
            "specialist_name",
            "curriculum_position",
            "position_code",
            "targeted_positions",
            "status",
            "intervention_part",
            "scheduled_start",
            "started_at",
            "ended_at",
            "activities_completed",
            "item_sets",
            "accuracy_rate",
            "accuracy_numerator",
            "accuracy_denominator",
            "time_to_mastery_signals",
            "error_patterns",
            "behavioral_observations",
            "next_session_direction",
            "home_practice_suggestion",
            "notes",
            "revision",
            "created_by",
            "updated_by",
            "revision_history",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "center",
            "child_name",
            "specialist",
            "specialist_name",
            "position_code",
            "revision",
            "created_by",
            "updated_by",
            "revision_history",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {"intervention_part": {"required": False}}

    def validate(self, attrs):
        request = self.context["request"]
        child = attrs.get("child", getattr(self.instance, "child", None))
        if not user_can_evaluate_child(request.user, child):
            raise serializers.ValidationError("You are not assigned to this child's center.")

        placement = (
            StudentPlacement.objects.filter(child=child, is_active=True, is_deleted=False)
            .select_related("center", "curriculum", "current_position__curriculum")
            .first()
        )
        position = attrs.get("curriculum_position", getattr(self.instance, "curriculum_position", None))
        if position is None:
            if placement is None:
                raise serializers.ValidationError(
                    {"curriculum_position": "An active placement is required to default the session position."}
                )
            attrs["curriculum_position"] = placement.current_position
            position = placement.current_position

        if placement and position.curriculum_id != placement.curriculum_id:
            raise serializers.ValidationError(
                {"curriculum_position": "The session position must use the child's active methodology."}
            )

        if "intervention_part" not in attrs and self.instance is None:
            attrs["intervention_part"] = self._default_intervention_part(child, position)
        if attrs.get("accuracy_rate") is None:
            numerator = attrs.get("accuracy_numerator")
            denominator = attrs.get("accuracy_denominator")
            if numerator is not None and denominator:
                attrs["accuracy_rate"] = round((numerator / denominator) * 100, 2)

        targeted_positions = attrs.get("targeted_positions")
        status_value = attrs.get("status", getattr(self.instance, "status", Session.Status.SCHEDULED))
        if status_value == Session.Status.COMPLETED and targeted_positions == []:
            raise serializers.ValidationError({"targeted_positions": "A completed session requires at least one target."})
        if targeted_positions:
            invalid = [target.id for target in targeted_positions if target.curriculum_id != position.curriculum_id]
            if invalid:
                raise serializers.ValidationError(
                    {"targeted_positions": "Every target must belong to the session curriculum."}
                )
        if status_value == Session.Status.COMPLETED:
            self._validate_item_capture(attrs, position)
        return attrs

    def _validate_item_capture(self, attrs, position):
        item_sets = attrs.get("item_sets", getattr(self.instance, "item_sets", {}))
        captured_items = []
        item_set_ids = set()
        for item_set in item_sets.values():
            if not isinstance(item_set, dict):
                raise serializers.ValidationError({"item_sets": "Each item set must be a structured object."})
            if item_set.get("item_set_id"):
                item_set_ids.add(item_set["item_set_id"])
            items = item_set.get("items")
            if not isinstance(items, list) or not items:
                raise serializers.ValidationError({"item_sets": "Completed item sets require item-level outcomes."})
            captured_items.extend(items)

        for activity in attrs.get("activities_completed", getattr(self.instance, "activities_completed", [])):
            if isinstance(activity, dict) and activity.get("item_set_id"):
                item_set_ids.add(activity["item_set_id"])
        if not item_set_ids:
            raise serializers.ValidationError({"item_sets": "At least one stable item-set ID is required."})

        common_required = {"item_id", "correct", "mode", "prompt_level"}
        for item in captured_items:
            if not isinstance(item, dict) or not common_required.issubset(item):
                raise serializers.ValidationError(
                    {"item_sets": "Every item requires item_id, correct, mode, and prompt_level."}
                )
            if position.curriculum.code == "pfr" and "latency_seconds" not in item and "timeout" not in item:
                raise serializers.ValidationError(
                    {"item_sets": "Every PFR item requires latency_seconds or an explicit timeout."}
                )
            if position.curriculum.code == "og_plus":
                code = item.get("position_code")
                if not code or not position.curriculum.positions.filter(code=code, is_deleted=False).exists():
                    raise serializers.ValidationError(
                        {"item_sets": "Every OG+ item must link to a position in the active curriculum."}
                    )

        previous_ids = set()
        prior_sessions = Session.objects.filter(
            child=attrs.get("child", getattr(self.instance, "child", None)),
            curriculum_position=position,
            status=Session.Status.COMPLETED,
            is_deleted=False,
        )
        if self.instance:
            prior_sessions = prior_sessions.exclude(pk=self.instance.pk)
        for prior in prior_sessions.only("activities_completed", "item_sets"):
            previous_ids.update(
                activity.get("item_set_id")
                for activity in prior.activities_completed
                if isinstance(activity, dict) and activity.get("item_set_id")
            )
            previous_ids.update(
                value.get("item_set_id")
                for value in prior.item_sets.values()
                if isinstance(value, dict) and value.get("item_set_id")
            )
        reused = sorted(item_set_ids & previous_ids)
        if reused:
            raise serializers.ValidationError(
                {"item_sets": f"Completed sessions must use distinct item sets; already used: {', '.join(reused)}."}
            )

    @staticmethod
    def _default_intervention_part(child, position):
        if position.curriculum.code == "og_plus":
            return Session.InterventionPart.OG_CONCEPT
        latest = (
            Session.objects.filter(
                child=child,
                curriculum_position=position,
                status=Session.Status.COMPLETED,
                is_deleted=False,
            )
            .order_by("-ended_at", "-created_at")
            .first()
        )
        if latest and latest.intervention_part == Session.InterventionPart.PFR_1A:
            return Session.InterventionPart.PFR_1B
        return Session.InterventionPart.PFR_1A

    @transaction.atomic
    def create(self, validated_data):
        targeted_positions = validated_data.pop("targeted_positions", None)
        request = self.context["request"]
        child = validated_data["child"]
        position = validated_data["curriculum_position"]
        session = Session(
            **validated_data,
            center=child.school or position.center,
            specialist=request.user,
            created_by=request.user,
            updated_by=request.user,
        )
        self._full_clean(session)
        session.save()
        session.targeted_positions.set(targeted_positions or [position])
        return session

    @transaction.atomic
    def update(self, instance, validated_data):
        targeted_positions = validated_data.pop("targeted_positions", None)
        for name, value in validated_data.items():
            setattr(instance, name, value)
        instance.updated_by = self.context["request"].user
        self._full_clean(instance)
        instance.save()
        if targeted_positions is not None:
            instance.targeted_positions.set(targeted_positions)
        return instance

    @staticmethod
    def _full_clean(session):
        try:
            session.full_clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
