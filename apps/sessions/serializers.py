from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from apps.api.permissions import user_can_log_session
from apps.curriculum.models import CurriculumSequence, StudentPlacement
from apps.sessions.models import Session, SessionRevision, SessionTemplate, SkillObservation
from apps.sessions.options import BEHAVIORAL_OBSERVATION_OPTIONS, BEHAVIORAL_RATING_OPTIONS, ERROR_PATTERN_OPTIONS
from apps.sessions.rapid_logging import default_intervention_part, expand_quick_complete
from apps.sessions.services import apply_template_defaults, resolve_session_template
from apps.users.models import ChildProfile, CustomUser


class SessionRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionRevision
        fields = ["id", "revision", "changed_by", "snapshot", "created_at"]
        read_only_fields = fields


class SessionTemplateSerializer(serializers.ModelSerializer):
    curriculum_code = serializers.CharField(source="curriculum.code", read_only=True)
    position_code = serializers.CharField(source="curriculum_position.code", read_only=True)

    class Meta:
        model = SessionTemplate
        fields = [
            "id",
            "center",
            "curriculum",
            "curriculum_code",
            "curriculum_position",
            "position_code",
            "session_part",
            "capture_fields",
            "title",
            "is_active",
            "version",
            "metadata",
            "revision",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "center",
            "curriculum_code",
            "position_code",
            "revision",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        request = self.context["request"]
        curriculum = attrs.get("curriculum", getattr(self.instance, "curriculum", None))
        position = attrs.get("curriculum_position", getattr(self.instance, "curriculum_position", None))
        if curriculum is None:
            raise serializers.ValidationError({"curriculum": "This field is required."})
        user = request.user
        can_manage_center = (
            user.is_superuser
            or getattr(user, "role", None) == CustomUser.Role.SUPER_ADMIN
            or curriculum.center.memberships.filter(user=user, is_deleted=False).exists()
        )
        if not can_manage_center:
            raise serializers.ValidationError("You are not assigned to this center.")
        if position and position.curriculum_id != curriculum.id:
            raise serializers.ValidationError(
                {"curriculum_position": "The position must belong to the selected curriculum."}
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        template = SessionTemplate(
            **validated_data,
            center=validated_data["curriculum"].center,
            created_by=request.user,
            updated_by=request.user,
        )
        self._full_clean(template)
        template.save()
        return template

    @transaction.atomic
    def update(self, instance, validated_data):
        for name, value in validated_data.items():
            setattr(instance, name, value)
        instance.center = instance.curriculum.center
        instance.updated_by = self.context["request"].user
        self._full_clean(instance)
        instance.save()
        return instance

    @staticmethod
    def _full_clean(template):
        try:
            template.full_clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error


class SkillObservationSerializer(serializers.ModelSerializer):
    child_name = serializers.CharField(source="child.__str__", read_only=True)
    position_code = serializers.CharField(source="curriculum_position.code", read_only=True)

    class Meta:
        model = SkillObservation
        fields = [
            "id",
            "center",
            "session",
            "child",
            "child_name",
            "curriculum_position",
            "position_code",
            "accuracy_rate",
            "response_rating",
            "error_pattern_tags",
            "time_signals",
            "activities",
            "item_references",
            "source_session_revision",
            "metadata",
            "revision",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SessionSerializer(serializers.ModelSerializer):
    client_request_id = serializers.UUIDField(required=False)
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
            "client_request_id",
            "center",
            "child",
            "child_name",
            "specialist",
            "specialist_name",
            "curriculum_position",
            "position_code",
            "session_template",
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
            "session_template",
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
        if not user_can_log_session(request.user, child, session=self.instance):
            raise serializers.ValidationError("You are not authorized to log sessions for this reader.")

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
        session_part = attrs.get("intervention_part", getattr(self.instance, "intervention_part", None))
        position_changed = self.instance is not None and position.pk != self.instance.curriculum_position_id
        part_changed = self.instance is not None and session_part != self.instance.intervention_part
        if self.instance is None or position_changed or part_changed:
            attrs["session_template"] = resolve_session_template(position, session_part)
        elif self.instance.session_template_id:
            attrs["session_template"] = self.instance.session_template
        else:
            attrs["session_template"] = resolve_session_template(position, session_part)
        if self.instance is None:
            apply_template_defaults(attrs, attrs["session_template"])
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
        return default_intervention_part(child, position)

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


class RapidSessionLogSerializer(serializers.Serializer):
    class Mode:
        QUICK_COMPLETE = "quick_complete"
        FULL_DETAIL = "full_detail"

    mode = serializers.ChoiceField(
        choices=[(Mode.QUICK_COMPLETE, "Quick complete"), (Mode.FULL_DETAIL, "Full detail")],
        default=Mode.QUICK_COMPLETE,
    )
    child = serializers.PrimaryKeyRelatedField(
        queryset=ChildProfile.objects.filter(is_deleted=False).select_related("school"),
        required=False,
    )
    child_id = serializers.IntegerField(write_only=True, required=False)
    session_id = serializers.IntegerField(required=False)
    client_request_id = serializers.UUIDField(required=False)
    accuracy_numerator = serializers.IntegerField(min_value=0, required=False)
    accuracy_denominator = serializers.IntegerField(min_value=1, required=False)
    accuracy_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False
    )
    duration_minutes = serializers.IntegerField(min_value=1, max_value=240, default=60)
    scheduled_start = serializers.DateTimeField(required=False)
    activity_codes = serializers.ListField(child=serializers.CharField(max_length=80), required=False)
    error_pattern_codes = serializers.ListField(
        child=serializers.ChoiceField(choices=ERROR_PATTERN_OPTIONS), required=False
    )
    behavioral_observation_codes = serializers.ListField(
        child=serializers.ChoiceField(choices=BEHAVIORAL_OBSERVATION_OPTIONS), required=False
    )
    behavioral_rating = serializers.ChoiceField(
        choices=BEHAVIORAL_RATING_OPTIONS, default="consistent"
    )
    next_session_direction = serializers.CharField(required=False, allow_blank=True)
    home_practice_suggestion = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    full_detail = serializers.JSONField(required=False)

    def validate(self, attrs):
        request = self.context["request"]
        session = None
        if attrs.get("session_id"):
            session = (
                Session.objects.filter(pk=attrs["session_id"], is_deleted=False)
                .select_related("child__school", "specialist", "curriculum_position__curriculum")
                .first()
            )
            if session is None:
                raise serializers.ValidationError({"session_id": "Session not found."})
        child = attrs.get("child")
        if child is None and attrs.get("child_id"):
            child = ChildProfile.objects.filter(pk=attrs["child_id"], is_deleted=False).first()
        child = child or getattr(session, "child", None)
        if child is None:
            raise serializers.ValidationError({"child": "Select a reader."})
        if session and session.child_id != child.id:
            raise serializers.ValidationError({"child": "The selected reader does not match this session."})
        if not user_can_log_session(request.user, child, session):
            raise serializers.ValidationError("You are not authorized to log sessions for this reader.")
        if attrs["mode"] == self.Mode.FULL_DETAIL:
            payload = attrs.get("full_detail")
            if not isinstance(payload, dict):
                raise serializers.ValidationError({"full_detail": "Full-detail mode requires a session object."})
            payload = {**payload, "child": child.id}
        else:
            numerator = attrs.get("accuracy_numerator")
            denominator = attrs.get("accuracy_denominator")
            percentage = attrs.get("accuracy_percentage")
            if percentage is not None and numerator is None and denominator is None:
                numerator, denominator = int(percentage.quantize(1)), 100
            if numerator is None or denominator is None:
                raise serializers.ValidationError(
                    {"accuracy_numerator": "Enter correct and attempted counts, or an accuracy percentage."}
                )
            if numerator > denominator:
                raise serializers.ValidationError(
                    {"accuracy_numerator": "Correct responses cannot exceed attempted responses."}
                )
            try:
                payload = expand_quick_complete(
                    child=child,
                    specialist=request.user,
                    accuracy_numerator=numerator,
                    accuracy_denominator=denominator,
                    duration_minutes=attrs["duration_minutes"],
                    scheduled_start=attrs.get("scheduled_start"),
                    activity_codes=attrs.get("activity_codes"),
                    error_pattern_codes=attrs.get("error_pattern_codes"),
                    behavioral_observation_codes=attrs.get("behavioral_observation_codes"),
                    behavioral_rating=attrs["behavioral_rating"],
                    next_session_direction=attrs.get("next_session_direction", ""),
                    home_practice_suggestion=attrs.get("home_practice_suggestion", ""),
                    notes=attrs.get("notes", ""),
                    session=session,
                )
            except DjangoValidationError as error:
                detail = error.message_dict if hasattr(error, "message_dict") else {"detail": error.messages}
                raise serializers.ValidationError(detail) from error
        if attrs.get("client_request_id") and session is None:
            payload["client_request_id"] = attrs["client_request_id"]
        self._session_serializer = SessionSerializer(
            instance=session,
            data=payload,
            partial=session is not None,
            context=self.context,
        )
        self._session_serializer.is_valid(raise_exception=True)
        return attrs

    def create(self, validated_data):
        return self._session_serializer.save()
