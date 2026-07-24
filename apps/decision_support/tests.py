from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.curriculum.models import Curriculum, CurriculumSequence
from apps.schools.models import School, SchoolMembership
from apps.sessions.models import Session
from apps.users.models import ChildProfile, CustomUser

from .models import Flag, Milestone, OutcomeAggregate, Prediction
from .serializers import (
    FlagSerializer,
    MilestoneSerializer,
    OutcomeAggregateSerializer,
    PredictionSerializer,
)
from .services import evaluate_flags_for_session, generate_basic_prediction, run_outcomes_aggregation


class DecisionSupportModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.center = School.objects.create(
            schema_name="decision_support_center",
            name="Decision Support Center",
            slug="decision-support-center",
        )
        cls.other_center = School.objects.create(
            schema_name="other_decision_support_center",
            name="Other Decision Support Center",
            slug="other-decision-support-center",
        )
        cls.leader = CustomUser.objects.create_user(
            username="decision-support-leader",
            email="decision-support-leader@example.com",
            password="test-password",
            role=CustomUser.Role.SCHOOL_ADMIN,
        )
        SchoolMembership.objects.create(
            school=cls.center,
            user=cls.leader,
            role=SchoolMembership.Role.ADMIN,
        )
        cls.child = ChildProfile.objects.create(
            first_name="Learner",
            school=cls.center,
            grade_level=ChildProfile.GradeLevel.GRADE_2,
        )
        cls.other_child = ChildProfile.objects.create(
            first_name="Other Learner",
            school=cls.other_center,
            grade_level=ChildProfile.GradeLevel.GRADE_2,
        )
        cls.curriculum = Curriculum.objects.create(
            center=cls.center,
            code=Curriculum.Code.PFR,
            name="Phonics for Reading",
        )
        cls.position = CurriculumSequence.objects.create(
            center=cls.center,
            curriculum=cls.curriculum,
            code="PFR-A-01",
            sequence_order=1,
            level=CurriculumSequence.PFRLevel.A,
            lesson_number=1,
            title="Lesson 1",
            position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
        )
        cls.session = Session.objects.create(
            center=cls.center,
            child=cls.child,
            specialist=cls.leader,
            curriculum_position=cls.position,
            intervention_part=Session.InterventionPart.PFR_1A,
            scheduled_start=timezone.make_aware(datetime(2026, 7, 1, 14)),
        )

    def test_all_four_models_can_be_created(self):
        milestone = Milestone.objects.create(
            center=self.center,
            child=self.child,
            definition="Complete the current instructional sequence position.",
            curriculum_position=self.position,
            target_date=date(2026, 8, 1),
        )
        flag = Flag.objects.create(
            center=self.center,
            child=self.child,
            code=Flag.Code.FLAT_ACCURACY,
            trigger_rule={"minimum_captures": 4, "maximum_gain_points": 5},
            evidence_snapshot={"session_ids": [self.session.pk]},
            related_session=self.session,
            curriculum_position=self.position,
            routed_to=self.leader,
            model_or_rule_version="instructional-design-2026.1",
        )
        prediction = Prediction.objects.create(
            center=self.center,
            child=self.child,
            target_milestone=milestone,
            estimated_sessions=4,
            confidence=Decimal("0.700"),
            model_version="schema-only-v0",
            evidence={"session_ids": [self.session.pk]},
        )
        aggregate = OutcomeAggregate.objects.create(
            center=self.center,
            dimension=OutcomeAggregate.Dimension.METHODOLOGY,
            dimension_value=Curriculum.Code.PFR,
            metric_name="milestone_achievement_rate",
            value=Decimal("0.7500"),
            cohort_size=8,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        )

        self.assertEqual(flag.child, self.child)
        self.assertEqual(prediction.target_milestone, milestone)
        self.assertEqual(milestone.curriculum_position, self.position)
        self.assertEqual(aggregate.cohort_size, 8)

    def test_outcome_aggregate_has_no_child_or_staff_pii_fields(self):
        field_names = {field.name for field in OutcomeAggregate._meta.get_fields()}
        forbidden_fields = {
            "child",
            "student",
            "specialist",
            "staff",
            "user",
            "created_by",
            "updated_by",
            "first_name",
            "last_name",
            "email",
            "student_identifier",
        }

        self.assertTrue(forbidden_fields.isdisjoint(field_names))
        relation_names = {
            field.name
            for field in OutcomeAggregate._meta.get_fields()
            if field.is_relation
        }
        self.assertEqual(relation_names, {"center"})

    def test_outcome_privacy_floor_rejects_single_child_cohorts(self):
        aggregate = OutcomeAggregate(
            center=self.center,
            dimension=OutcomeAggregate.Dimension.GRADE_BAND,
            dimension_value=ChildProfile.GradeLevel.GRADE_2,
            metric_name="mastery_rate",
            value=Decimal("1.0000"),
            cohort_size=1,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        )

        with self.assertRaises(ValidationError):
            aggregate.full_clean()

    def test_cross_center_child_relationship_is_rejected(self):
        milestone = Milestone(
            center=self.center,
            child=self.other_child,
            definition="Complete an instructional skill band.",
            skill_band="phonics",
            target_date=date(2026, 8, 1),
        )

        with self.assertRaises(ValidationError):
            milestone.full_clean()

    def test_service_stubs_do_not_populate_v2_records(self):
        self.assertEqual(evaluate_flags_for_session(self.session), [])
        self.assertIsNone(generate_basic_prediction(self.child))
        self.assertEqual(run_outcomes_aggregation((date(2026, 7, 1), date(2026, 7, 31))), [])

    def test_serializers_are_read_only(self):
        for serializer_class in (
            FlagSerializer,
            PredictionSerializer,
            MilestoneSerializer,
            OutcomeAggregateSerializer,
        ):
            self.assertTrue(all(field.read_only for field in serializer_class().fields.values()))


class DecisionSupportApiIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.center = School.objects.create(
            schema_name="api_decision_support_center",
            name="API Decision Support Center",
            slug="api-decision-support-center",
        )
        cls.other_center = School.objects.create(
            schema_name="api_other_decision_support_center",
            name="API Other Decision Support Center",
            slug="api-other-decision-support-center",
        )
        cls.leader = CustomUser.objects.create_user(
            username="api-outcomes-leader",
            email="api-outcomes-leader@example.com",
            password="test-password",
            role=CustomUser.Role.SCHOOL_ADMIN,
        )
        SchoolMembership.objects.create(
            school=cls.center,
            user=cls.leader,
            role=SchoolMembership.Role.OWNER,
        )
        cls.own_aggregate = OutcomeAggregate.objects.create(
            center=cls.center,
            dimension=OutcomeAggregate.Dimension.CENTER,
            dimension_value=cls.center.slug,
            metric_name="session_completion_rate",
            value=Decimal("0.9000"),
            cohort_size=10,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        )
        cls.other_aggregate = OutcomeAggregate.objects.create(
            center=cls.other_center,
            dimension=OutcomeAggregate.Dimension.CENTER,
            dimension_value=cls.other_center.slug,
            metric_name="session_completion_rate",
            value=Decimal("0.8000"),
            cohort_size=10,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.leader)

    def test_outcomes_endpoint_is_center_isolated(self):
        response = self.client.get(reverse("api:decision_support:outcome-list"))

        self.assertEqual(response.status_code, 200)
        result_ids = {result["id"] for result in response.data["results"]}
        self.assertEqual(result_ids, {self.own_aggregate.pk})
        self.assertNotIn(self.other_aggregate.pk, result_ids)

    def test_outcomes_endpoint_rejects_non_leadership_staff(self):
        specialist = CustomUser.objects.create_user(
            username="api-outcomes-specialist",
            email="api-outcomes-specialist@example.com",
            password="test-password",
            role=CustomUser.Role.TEACHER,
        )
        SchoolMembership.objects.create(
            school=self.center,
            user=specialist,
            role=SchoolMembership.Role.SPECIALIST,
        )
        self.client.force_authenticate(specialist)

        response = self.client.get(reverse("api:decision_support:outcome-list"))

        self.assertEqual(response.status_code, 403)

    def test_outcomes_endpoint_is_read_only(self):
        response = self.client.post(
            reverse("api:decision_support:outcome-list"),
            {
                "center": self.center.pk,
                "dimension": OutcomeAggregate.Dimension.CENTER,
                "dimension_value": self.center.slug,
                "metric_name": "unreviewed_metric",
                "value": "1.0000",
                "cohort_size": 10,
                "period_start": "2026-07-01",
                "period_end": "2026-07-31",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 405)
