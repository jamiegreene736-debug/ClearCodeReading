from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.utils import timezone

from apps.sessions.models import Session


class SessionModelTests(SimpleTestCase):
    def test_session_capture_has_required_structured_fields(self):
        field_names = {field.name for field in Session._meta.get_fields()}
        self.assertTrue(
            {
                "targeted_positions",
                "activities_completed",
                "accuracy_rate",
                "accuracy_numerator",
                "accuracy_denominator",
                "time_to_mastery_signals",
                "error_patterns",
                "behavioral_observations",
                "next_session_direction",
                "home_practice_suggestion",
                "item_sets",
            }.issubset(field_names)
        )

    def test_accuracy_rate_is_bounded(self):
        field = Session._meta.get_field("accuracy_rate")
        with self.assertRaises(ValidationError):
            field.run_validators(Decimal("101"))

    def test_intervention_parts_are_explicit(self):
        self.assertEqual(
            set(Session.InterventionPart.values),
            {"pfr_1a", "pfr_1b", "og_concept"},
        )

    def test_completed_session_requires_outcome_capture(self):
        session = Session(
            status=Session.Status.COMPLETED,
            intervention_part=Session.InterventionPart.PFR_1A,
            scheduled_start=timezone.now(),
        )
        with self.assertRaises(ValidationError) as error:
            session.clean()
        self.assertIn("accuracy_rate", error.exception.message_dict)
        self.assertIn("item_sets", error.exception.message_dict)

    def test_completed_session_validates_observable_behavior_codes(self):
        now = timezone.now()
        session = Session(
            status=Session.Status.COMPLETED,
            intervention_part=Session.InterventionPart.PFR_1A,
            scheduled_start=now,
            started_at=now,
            ended_at=now + timezone.timedelta(minutes=60),
            activities_completed=[
                {"code": "word_reading", "status": "completed", "minutes": 10, "item_set_id": "set-1"}
            ],
            item_sets={"word_reading": {"correct": 9, "total": 10}},
            accuracy_rate=90,
            accuracy_numerator=9,
            accuracy_denominator=10,
            time_to_mastery_signals={"reteach": False},
            behavioral_observations=[{"code": "unsupported_label", "rating": "consistent"}],
            next_session_direction="Continue the next item set.",
            home_practice_suggestion="Practice for five minutes.",
        )
        with self.assertRaises(ValidationError) as error:
            session.clean()
        self.assertIn("behavioral_observations", error.exception.message_dict)
