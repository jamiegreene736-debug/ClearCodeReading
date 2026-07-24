from datetime import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.curriculum.models import Curriculum, CurriculumSequence, StudentPlacement
from apps.outcomes.models import DeIdentifiedOutcomeSnapshot
from apps.outcomes.services import OutcomeWindow, aggregate_outcomes
from apps.progress.models import MasteryRecord, Progress
from apps.schools.models import School, SchoolMembership
from apps.sessions.models import Session
from apps.users.models import AuditLog, ChildProfile, CustomUser
from apps.curriculum.models import Skill


@override_settings(OUTCOMES_MIN_COHORT_SIZE=2)
class OutcomesAggregationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        original = School.auto_create_schema
        School.auto_create_schema = False
        try:
            cls.center = School.objects.create(name="Launch Center", slug="launch-outcomes", schema_name="launch_outcomes")
            cls.other_center = School.objects.create(name="West Center", slug="west-outcomes", schema_name="west_outcomes")
        finally:
            School.auto_create_schema = original

        cls.super_admin = CustomUser.objects.create_user(
            username="outcomes-super",
            email="outcomes-super@example.com",
            role=CustomUser.Role.SUPER_ADMIN,
        )
        cls.school_admin = CustomUser.objects.create_user(
            username="outcomes-admin",
            email="outcomes-admin@example.com",
            role=CustomUser.Role.SCHOOL_ADMIN,
        )
        cls.guardian = CustomUser.objects.create_user(
            username="outcomes-guardian",
            email="outcomes-guardian@example.com",
            role=CustomUser.Role.GUARDIAN,
        )
        cls.specialist = CustomUser.objects.create_user(
            username="outcomes-specialist",
            email="specialist@example.com",
            role=CustomUser.Role.TEACHER,
            first_name="Specialist",
            last_name="Private",
        )
        SchoolMembership.objects.create(
            school=cls.center,
            user=cls.school_admin,
            role=SchoolMembership.Role.ADMIN,
        )

        cls.curriculum = Curriculum.objects.create(center=cls.center, code=Curriculum.Code.PFR, name="PFR")
        cls.other_curriculum = Curriculum.objects.create(center=cls.other_center, code=Curriculum.Code.PFR, name="PFR")
        cls.position = cls._position(cls.center, cls.curriculum, "PFR-A-01", 1)
        cls.other_position = cls._position(cls.other_center, cls.other_curriculum, "PFR-A-01-W", 1)
        cls.skill = Skill.objects.create(code="outcomes-cvc", name="CVC words", domain=Skill.Domain.PHONICS)

        cls.child = ChildProfile.objects.create(first_name="Avery", last_name="Reader", school=cls.center, grade_level=ChildProfile.GradeLevel.GRADE_1)
        cls.peer = ChildProfile.objects.create(first_name="Blake", last_name="Reader", school=cls.center, grade_level=ChildProfile.GradeLevel.GRADE_2)
        cls.other_child = ChildProfile.objects.create(first_name="Casey", last_name="Reader", school=cls.other_center, grade_level=ChildProfile.GradeLevel.GRADE_1)
        cls.other_peer = ChildProfile.objects.create(first_name="Devon", last_name="Reader", school=cls.other_center, grade_level=ChildProfile.GradeLevel.GRADE_2)
        for child, curriculum, position, center in [
            (cls.child, cls.curriculum, cls.position, cls.center),
            (cls.peer, cls.curriculum, cls.position, cls.center),
            (cls.other_child, cls.other_curriculum, cls.other_position, cls.other_center),
            (cls.other_peer, cls.other_curriculum, cls.other_position, cls.other_center),
        ]:
            StudentPlacement.objects.create(
                center=center,
                child=child,
                curriculum=curriculum,
                current_position=position,
                methodology_rationale="Evidence.",
                placed_at=cls._dt(2026, 1, 1),
            )
            progress = Progress.objects.create(child=child, school=center, skill=cls.skill, status=Progress.Status.DEVELOPING)
            if child != cls.peer:
                MasteryRecord.objects.create(
                    child=child,
                    skill=cls.skill,
                    progress=progress,
                    mastered_at=cls._dt(2026, 2, 20),
                    mastered_by=cls.specialist,
                    score=95,
                )

        cls._session(cls.center, cls.child, cls.position, cls._dt(2026, 1, 10), numerator=9, denominator=10)
        cls._session(cls.center, cls.child, cls.position, cls._dt(2026, 2, 10), numerator=8, denominator=10)
        cls._session(cls.center, cls.peer, cls.position, cls._dt(2026, 2, 12), numerator=7, denominator=10)
        cls._session(cls.other_center, cls.other_child, cls.other_position, cls._dt(2026, 2, 12), numerator=10, denominator=10)

    def setUp(self):
        self.client = APIClient()
        self.window = OutcomeWindow(
            window_type=DeIdentifiedOutcomeSnapshot.WindowType.QUARTER,
            start=timezone.datetime(2026, 1, 1).date(),
            end=timezone.datetime(2026, 3, 31).date(),
        )

    def test_aggregation_produces_deidentified_metrics(self):
        snapshots = aggregate_outcomes(window=self.window, aggregate_version="test-v1")
        center_snapshot = DeIdentifiedOutcomeSnapshot.objects.get(
            center=self.center,
            methodology=Curriculum.Code.PFR,
            grade_band="grade_1_2",
            aggregate_version="test-v1",
        )

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(center_snapshot.metrics["cohort_students"], 2)
        self.assertEqual(center_snapshot.metrics["completed_sessions"], 3)
        self.assertEqual(center_snapshot.metrics["students_with_mastery"], 1)
        self.assertEqual(center_snapshot.metrics["skill_mastery_rate"], 50.0)
        self.assertEqual(center_snapshot.metrics["mean_sessions_to_mastery"], 2.0)
        self.assertEqual(center_snapshot.metrics["weighted_accuracy_rate"], 80.0)
        self.assertEqual(center_snapshot.privacy_floor, 2)
        self.assertEqual(center_snapshot.source_counts["skill_observations"], 3)
        self.assertNotIn("Avery", str(center_snapshot.metrics))
        self.assertNotIn("specialist@example.com", str(center_snapshot.metrics))
        self.assertEqual(center_snapshot.center_key, DeIdentifiedOutcomeSnapshot.objects.get(id=center_snapshot.id).center_key)

    def test_aggregation_is_idempotent_for_same_version_and_window(self):
        first = aggregate_outcomes(window=self.window, aggregate_version="stable-v1")
        second = aggregate_outcomes(window=self.window, aggregate_version="stable-v1")

        self.assertEqual([snapshot.id for snapshot in first], [snapshot.id for snapshot in second])
        self.assertEqual(DeIdentifiedOutcomeSnapshot.objects.filter(aggregate_version="stable-v1").count(), 2)

    def test_aggregation_respects_privacy_floor(self):
        snapshots = aggregate_outcomes(
            window=self.window,
            aggregate_version="privacy-v1",
            min_cohort_size=3,
        )

        self.assertEqual(snapshots, [])
        self.assertFalse(DeIdentifiedOutcomeSnapshot.objects.filter(aggregate_version="privacy-v1").exists())

    def test_snapshot_rejects_identifier_keys(self):
        with self.assertRaises(ValidationError):
            DeIdentifiedOutcomeSnapshot.objects.create(
                center=self.center,
                methodology=Curriculum.Code.PFR,
                grade_band="grade_1_2",
                window_type=DeIdentifiedOutcomeSnapshot.WindowType.QUARTER,
                window_start=self.window.start,
                window_end=self.window.end,
                aggregate_version="pii-v1",
                privacy_floor=2,
                metrics={"cohort_students": 2, "child_ids": [self.child.id, self.peer.id]},
            )

    def test_snapshot_is_immutable(self):
        aggregate_outcomes(window=self.window, aggregate_version="immutable-v1")
        snapshot = DeIdentifiedOutcomeSnapshot.objects.get(aggregate_version="immutable-v1", center=self.center)
        snapshot.metrics = {"changed": True}

        with self.assertRaises(ValueError):
            snapshot.save()

    def test_reporting_api_is_deidentified_and_audited(self):
        aggregate_outcomes(window=self.window, aggregate_version="api-v1")
        self.client.force_authenticate(self.school_admin)

        response = self.client.get("/api/v1/outcomes/snapshots/")

        self.assertEqual(response.status_code, 200, response.data)
        payload = response.json()
        response_text = str(payload)
        self.assertNotIn("Avery", response_text)
        self.assertNotIn("Blake", response_text)
        self.assertNotIn("Specialist", response_text)
        self.assertNotIn("specialist@example.com", response_text)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["center_key"], DeIdentifiedOutcomeSnapshot.objects.get(center=self.center).center_key)
        self.assertTrue(AuditLog.objects.filter(action="outcomes.snapshots.list", actor=self.school_admin).exists())

    def test_reporting_api_is_center_scoped_for_school_admins(self):
        aggregate_outcomes(window=self.window, aggregate_version="scoped-v1")
        self.client.force_authenticate(self.school_admin)

        response = self.client.get("/api/v1/outcomes/snapshots/")

        self.assertEqual(response.status_code, 200, response.data)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 1)
        self.assertNotEqual(payload["results"][0]["center_key"], DeIdentifiedOutcomeSnapshot.objects.get(center=self.other_center).center_key)

    def test_non_leadership_user_cannot_access_outcomes(self):
        aggregate_outcomes(window=self.window, aggregate_version="forbidden-v1")
        self.client.force_authenticate(self.guardian)

        response = self.client.get("/api/v1/outcomes/snapshots/")

        self.assertEqual(response.status_code, 403)

    @classmethod
    def _position(cls, center, curriculum, code, order):
        return CurriculumSequence.objects.create(
            center=center,
            curriculum=curriculum,
            code=code,
            sequence_order=order,
            level="A",
            lesson_number=order,
            title=f"Lesson {order}",
            position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
        )

    @classmethod
    def _session(cls, center, child, position, at_time, *, numerator, denominator):
        return Session.objects.create(
            center=center,
            child=child,
            specialist=cls.specialist,
            curriculum_position=position,
            status=Session.Status.COMPLETED,
            intervention_part=Session.InterventionPart.PFR_1A,
            scheduled_start=at_time,
            started_at=at_time,
            ended_at=at_time + timezone.timedelta(minutes=45),
            activities_completed=[{"code": "decodable_reading", "status": "completed", "minutes": 10, "item_set_id": "d-1"}],
            item_sets={"decodable_text": {"item_set_id": "d-1", "type": "decodable_text", "title": "Practice", "items": []}},
            accuracy_rate=(numerator / denominator) * 100,
            accuracy_numerator=numerator,
            accuracy_denominator=denominator,
            time_to_mastery_signals={
                "cumulative_sessions_at_position": 1,
                "first_attempt_accuracy": numerator,
                "latest_accuracy": numerator,
                "prompts_per_10_items": 1,
                "independent_transfer": True,
                "reteach": False,
            },
            error_patterns=[],
            next_session_direction="Continue.",
            home_practice_suggestion="Practice.",
        )

    @staticmethod
    def _dt(year, month, day):
        return timezone.make_aware(datetime(year, month, day, 12, 0))
