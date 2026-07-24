from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.curriculum.models import Curriculum, CurriculumSequence, StudentPlacement
from apps.decision_support.engine import DeterministicDecisionSupportEngine
from apps.decision_support.models import GrowthFlag, MilestonePrediction
from apps.progress.dashboard import build_parent_dashboard
from apps.schools.models import School, SchoolMembership
from apps.sessions.models import Session
from apps.users.models import ChildProfile, CustomUser


class DecisionSupportEngineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        original = School.auto_create_schema
        School.auto_create_schema = False
        try:
            cls.center = School.objects.create(name="Decision Center", slug="decision", schema_name="decision")
            cls.other_center = School.objects.create(name="Other Center", slug="other-ds", schema_name="other_ds")
        finally:
            School.auto_create_schema = original
        cls.specialist = CustomUser.objects.create_user(
            username="ds-specialist",
            email="ds-specialist@example.com",
            role=CustomUser.Role.TEACHER,
        )
        cls.leader = CustomUser.objects.create_user(
            username="ds-leader",
            email="ds-leader@example.com",
            role=CustomUser.Role.SCHOOL_ADMIN,
        )
        SchoolMembership.objects.create(
            school=cls.center,
            user=cls.specialist,
            role=SchoolMembership.Role.SPECIALIST,
        )
        SchoolMembership.objects.create(
            school=cls.center,
            user=cls.leader,
            role=SchoolMembership.Role.OWNER,
        )
        cls.curriculum = Curriculum.objects.create(
            center=cls.center,
            code=Curriculum.Code.PFR,
            name="PFR",
            version="2026.1",
        )
        cls.positions = []
        for order in range(1, 6):
            cls.positions.append(
                CurriculumSequence.objects.create(
                    center=cls.center,
                    curriculum=cls.curriculum,
                    code=f"PFR-A-{order:02d}",
                    sequence_order=order,
                    level="A",
                    lesson_number=order,
                    title=f"Lesson {order}",
                    position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
                    mastery_criteria={"word_reading_accuracy_percent": 90},
                )
            )

    def setUp(self):
        self.engine = DeterministicDecisionSupportEngine()
        self.child = ChildProfile.objects.create(first_name="Avery", school=self.center)
        self.placement = StudentPlacement.objects.create(
            center=self.center,
            child=self.child,
            curriculum=self.curriculum,
            current_position=self.positions[0],
            methodology_rationale="Placement evidence.",
        )
        self.base_time = timezone.now() - timedelta(days=30)

    def _session(
        self,
        *,
        child=None,
        position=None,
        day=0,
        accuracy=80,
        reteach=False,
        error_code=None,
        mastered=False,
    ):
        position = position or self.positions[0]
        child = child or self.child
        start = self.base_time + timedelta(days=day)
        signals = {
            "cumulative_sessions_at_position": 1,
            "first_attempt_accuracy": accuracy,
            "latest_accuracy": accuracy,
            "prompts_per_10_items": 1,
            "independent_transfer": False,
            "reteach": reteach,
        }
        if mastered:
            signals["position_mastered"] = True
        return Session.objects.create(
            center=child.school,
            child=child,
            specialist=self.specialist,
            curriculum_position=position,
            status=Session.Status.COMPLETED,
            intervention_part=Session.InterventionPart.PFR_1A,
            scheduled_start=start,
            started_at=start,
            ended_at=start + timedelta(minutes=45),
            activities_completed=[
                {"code": "word_reading", "status": "completed", "minutes": 10, "item_set_id": f"set-{child.id}-{day}"}
            ],
            item_sets={"word_reading": {"item_set_id": f"set-{child.id}-{day}", "items": []}},
            accuracy_rate=accuracy,
            accuracy_numerator=int(accuracy),
            accuracy_denominator=100,
            time_to_mastery_signals=signals,
            error_patterns=(
                [{"code": error_code, "count": 2, "opportunities": 10}]
                if error_code
                else []
            ),
            behavioral_observations=[],
            next_session_direction="Continue the planned instructional sequence.",
            home_practice_suggestion="Practice the assigned word set.",
        )

    def _flag_codes(self, session):
        return {flag.flag_code for flag in self.engine.evaluate_completed_session(session.id)}

    def test_three_reteach_sessions_flag_contains_concrete_evidence_and_routes_leadership(self):
        self._session(day=1, reteach=True)
        self._session(day=2, reteach=True)
        latest = self._session(day=3, reteach=True)

        flags = self.engine.evaluate_completed_session(latest.id)

        flag = next(item for item in flags if item.flag_code == GrowthFlag.Code.THREE_RETEACH_SESSIONS)
        self.assertEqual(len(flag.evidence_snapshot["sessions"]), 3)
        self.assertIn("three consecutive", flag.explanation)
        self.assertEqual(set(flag.routed_to.all()), {self.specialist, self.leader})

    def test_flat_accuracy_flag_uses_four_capture_gain(self):
        sessions = [
            self._session(day=index, accuracy=accuracy)
            for index, accuracy in enumerate([70, 72, 73, 74], start=1)
        ]

        flags = self.engine.evaluate_completed_session(sessions[-1].id)

        flag = next(item for item in flags if item.flag_code == GrowthFlag.Code.FLAT_ACCURACY)
        self.assertEqual(flag.evidence_snapshot["percentage_point_gain"], 4.0)
        self.assertEqual([item["accuracy"] for item in flag.evidence_snapshot["sessions"]], [70.0, 72.0, 73.0, 74.0])

    def test_mastery_time_outlier_compares_curriculum_median(self):
        comparable_child = ChildProfile.objects.create(first_name="Jordan", school=self.center)
        StudentPlacement.objects.create(
            center=self.center,
            child=comparable_child,
            curriculum=self.curriculum,
            current_position=self.positions[1],
            methodology_rationale="Advanced after Lesson 1.",
        )
        self._session(child=comparable_child, day=1)
        self._session(child=comparable_child, day=2)
        target_sessions = [self._session(day=day) for day in range(1, 5)]

        flags = self.engine.evaluate_completed_session(target_sessions[-1].id)

        flag = next(item for item in flags if item.flag_code == GrowthFlag.Code.MASTERY_TIME_OUTLIER)
        self.assertEqual(flag.evidence_snapshot["completed_sessions"], 4)
        self.assertEqual(flag.evidence_snapshot["curriculum_median_sessions"], 2.0)

    def test_regression_after_mastery_requires_two_later_checks(self):
        mastered_at = self.base_time + timedelta(days=2)
        self.placement.current_position = self.positions[1]
        self.placement.placed_at = mastered_at
        self.placement.save(update_fields=["current_position", "placed_at", "updated_at"])
        self._session(position=self.positions[0], day=3, accuracy=85)
        latest = self._session(position=self.positions[0], day=4, accuracy=82)

        flags = self.engine.evaluate_completed_session(latest.id)

        flag = next(item for item in flags if item.flag_code == GrowthFlag.Code.REGRESSION_AFTER_MASTERY)
        self.assertEqual(flag.evidence_snapshot["promotion_threshold"], 90.0)
        self.assertEqual(flag.evidence_snapshot["accuracies"], [85.0, 82.0])

    def test_persistent_error_pattern_requires_three_consecutive_captures(self):
        self._session(day=1, error_code="short_vowel_confusion")
        self._session(day=2, error_code="short_vowel_confusion")
        latest = self._session(day=3, error_code="short_vowel_confusion")

        flags = self.engine.evaluate_completed_session(latest.id)

        flag = next(item for item in flags if item.flag_code == GrowthFlag.Code.ERROR_PATTERN_PERSISTENT)
        self.assertEqual(flag.evidence_snapshot["error_code"], "short_vowel_confusion")
        self.assertEqual(flag.evidence_snapshot["counts"], [2, 2, 2])

    def test_attendance_interruption_requires_more_than_fourteen_days(self):
        self._session(day=1)
        latest = self._session(day=16)

        flags = self.engine.evaluate_completed_session(latest.id)

        flag = next(item for item in flags if item.flag_code == GrowthFlag.Code.ATTENDANCE_INTERRUPTION)
        self.assertEqual(flag.evidence_snapshot["gap_days"], 15)

    def test_sparse_data_does_not_fire_any_flag(self):
        only_session = self._session(day=1, accuracy=60, reteach=True, error_code="short_vowel_confusion")

        self.assertEqual(self.engine.evaluate_completed_session(only_session.id), [])
        self.assertFalse(GrowthFlag.objects.exists())

    def test_growth_flag_rejects_cross_center_scope(self):
        other_child = ChildProfile.objects.create(first_name="Other", school=self.other_center)
        flag = GrowthFlag(
            center=self.center,
            child=other_child,
            position=self.positions[0],
            flag_code=GrowthFlag.Code.FLAT_ACCURACY,
            severity=GrowthFlag.Severity.MEDIUM,
            evidence_snapshot={},
            explanation="Review evidence.",
            advisory_recommendation="Specialist review.",
        )

        with self.assertRaises(ValidationError):
            flag.full_clean()

    def test_prediction_is_stored_with_explainable_uncertainty(self):
        self.placement.current_position = self.positions[2]
        self.placement.save(update_fields=["current_position", "updated_at"])
        self._session(position=self.positions[0], day=20)
        self._session(position=self.positions[0], day=21)
        self._session(position=self.positions[1], day=22)
        self._session(position=self.positions[1], day=23)
        self._session(position=self.positions[1], day=24)

        prediction = self.engine.generate_milestone_prediction(self.child.id)

        self.assertTrue(prediction.is_current)
        self.assertEqual(prediction.evidence_summary["sessions_per_position_source"], "child_history")
        self.assertEqual(prediction.evidence_summary["child_completed_position_counts"], [2, 3])
        self.assertGreaterEqual(prediction.upper_bound_sessions, prediction.predicted_sessions)
        self.assertIn("not a guarantee", prediction.disclaimer)
        self.assertIn("sessions", prediction.parent_timeline)

    def test_sparse_prediction_uses_methodology_default_and_dashboard_prefers_it(self):
        prediction = self.engine.generate_milestone_prediction(self.child.id)

        dashboard = build_parent_dashboard(self.child)

        self.assertEqual(prediction.confidence, MilestonePrediction.Confidence.LOW)
        self.assertEqual(prediction.evidence_summary["sessions_per_position_source"], "methodology_two_check_default")
        self.assertEqual(dashboard["milestone"]["status"], "prediction")
        self.assertEqual(dashboard["milestone"]["predicted_sessions"], prediction.predicted_sessions)
        self.assertIn("not a guarantee", dashboard["milestone"]["disclaimer"])

    def test_specialist_can_list_and_acknowledge_only_center_flags(self):
        flag = GrowthFlag.objects.create(
            center=self.center,
            child=self.child,
            trigger_session=self._session(day=1),
            position=self.positions[0],
            flag_code=GrowthFlag.Code.ATTENDANCE_INTERRUPTION,
            severity=GrowthFlag.Severity.MEDIUM,
            evidence_snapshot={"gap_days": 15},
            explanation="A 15-day gap was recorded.",
            advisory_recommendation="Recheck retained prerequisites.",
        )
        other_child = ChildProfile.objects.create(first_name="Other", school=self.other_center)
        other_curriculum = Curriculum.objects.create(
            center=self.other_center,
            code=Curriculum.Code.PFR,
            name="Other PFR",
        )
        other_position = CurriculumSequence.objects.create(
            center=self.other_center,
            curriculum=other_curriculum,
            code="PFR-A-01",
            sequence_order=1,
            level="A",
            lesson_number=1,
            title="Other Lesson",
            position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
        )
        GrowthFlag.objects.create(
            center=self.other_center,
            child=other_child,
            position=other_position,
            flag_code=GrowthFlag.Code.FLAT_ACCURACY,
            severity=GrowthFlag.Severity.MEDIUM,
            evidence_snapshot={},
            explanation="Other center evidence.",
            advisory_recommendation="Specialist review.",
        )
        client = APIClient()
        client.force_authenticate(self.specialist)

        list_response = client.get("/api/v1/growth-flags/?status=open")
        acknowledge_response = client.post(
            f"/api/v1/growth-flags/{flag.id}/acknowledge/",
            {"note": "Reviewed with the instructional team."},
            format="json",
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual({item["id"] for item in list_response.data["results"]}, {flag.id})
        self.assertEqual(acknowledge_response.status_code, 200, acknowledge_response.data)
        self.assertEqual(acknowledge_response.data["status"], GrowthFlag.Status.ACKNOWLEDGED)

    @patch("apps.sessions.signals.send_progress_report_to_parents.apply_async")
    @patch("apps.decision_support.signals.evaluate_completed_session.apply_async")
    def test_session_completion_schedules_decision_support(self, evaluate_enqueue, progress_enqueue):
        with self.captureOnCommitCallbacks(execute=True):
            session = self._session(day=1)

        evaluate_enqueue.assert_called_once_with(args=[session.id], ignore_result=True, retry=False)
        progress_enqueue.assert_called_once_with(args=[self.child.id], ignore_result=True, retry=False)
