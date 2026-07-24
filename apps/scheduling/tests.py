from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.curriculum.models import Curriculum, CurriculumSequence, StudentPlacement
from apps.scheduling.models import ProviderAvailability, ScheduleBooking, WaitlistEntry
from apps.scheduling.services import operations_metrics, ranked_group_suggestions
from apps.schools.models import School, SchoolMembership
from apps.users.models import ChildProfile, CustomUser


WINDOW = {"day_of_week": "monday", "start_time": "15:00", "end_time": "16:00", "timezone": "America/New_York"}


class SchedulingOptimizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        original = School.auto_create_schema
        School.auto_create_schema = False
        try:
            cls.center = School.objects.create(name="North", slug="phase2-north", schema_name="phase2_north")
            cls.other_center = School.objects.create(name="South", slug="phase2-south", schema_name="phase2_south")
        finally:
            School.auto_create_schema = original
        cls.admin = CustomUser.objects.create_user(username="ops", email="ops@example.com", role=CustomUser.Role.SCHOOL_ADMIN)
        cls.specialist = CustomUser.objects.create_user(username="schedule-specialist", email="schedule-specialist@example.com", role=CustomUser.Role.TEACHER)
        SchoolMembership.objects.create(school=cls.center, user=cls.admin, role=SchoolMembership.Role.ADMIN)
        SchoolMembership.objects.create(school=cls.center, user=cls.specialist, role=SchoolMembership.Role.SPECIALIST)
        cls.curriculum = Curriculum.objects.create(center=cls.center, code=Curriculum.Code.PFR, name="PFR")
        positions = []
        for order in [1, 2]:
            positions.append(CurriculumSequence.objects.create(
                center=cls.center,
                curriculum=cls.curriculum,
                code=f"PFR-A-0{order}",
                sequence_order=order,
                level="A",
                lesson_number=order,
                title=f"Lesson {order}",
                position_type=CurriculumSequence.PositionType.PHONICS_CONCEPT,
            ))
        cls.children = []
        for index, position in enumerate(positions):
            child = ChildProfile.objects.create(first_name=f"Reader {index}", school=cls.center, availability_windows=[WINDOW])
            StudentPlacement.objects.create(center=cls.center, child=child, curriculum=cls.curriculum, current_position=position, methodology_rationale="Evidence")
            cls.children.append(child)
        cls.pending_child = ChildProfile.objects.create(
            first_name="Pending",
            school=cls.center,
            availability_windows=[WINDOW],
            iep_status=ChildProfile.IEPStatus.ACTIVE,
            idea_parent_consent_status=ChildProfile.ApprovalStatus.PENDING,
            iep_team_approval_status=ChildProfile.ApprovalStatus.PENDING,
        )
        StudentPlacement.objects.create(center=cls.center, child=cls.pending_child, curriculum=cls.curriculum, current_position=positions[0], methodology_rationale="Evidence")
        ProviderAvailability.objects.create(center=cls.center, specialist=cls.specialist, windows=[WINDOW], max_group_size=3)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_ranked_grouping_matches_methodology_position_and_availability(self):
        suggestions = ranked_group_suggestions(self.center)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual({item["child"] for item in suggestions[0]["students"]}, {child.id for child in self.children})
        self.assertTrue(suggestions[0]["approval_required"])
        self.assertEqual(suggestions[0]["pending_authorizations"][0]["child"], self.pending_child.id)

    def test_booking_rejects_pending_iep_authorization(self):
        now = timezone.now()
        response = self.client.post("/api/v1/schedule-bookings/", {
            "center": self.center.id,
            "child": self.pending_child.id,
            "specialist": self.specialist.id,
            "starts_at": now.isoformat(),
            "ends_at": (now + timezone.timedelta(hours=1)).isoformat(),
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("IEP-aligned", str(response.data))

    def test_metrics_surface_expansion_thresholds(self):
        now = timezone.now()
        for child in self.children:
            ScheduleBooking.objects.create(
                center=self.center,
                child=child,
                specialist=self.specialist,
                starts_at=now,
                ends_at=now + timezone.timedelta(hours=2),
                status=ScheduleBooking.Status.CONFIRMED,
            )
        for index in range(25):
            child = ChildProfile.objects.create(first_name=f"Wait {index}", school=self.center)
            WaitlistEntry.objects.create(center=self.center, child=child, submarket="Northside" if index < 15 else "West")
        metrics = operations_metrics(self.center)
        self.assertTrue(metrics["waitlist_threshold_reached"])
        self.assertTrue(metrics["demand_concentration_threshold_reached"])
        self.assertEqual(metrics["top_submarket"], "Northside")

    def test_recommendation_endpoint_is_center_scoped(self):
        response = self.client.get(f"/api/v1/schedule-bookings/recommendations/?center={self.other_center.id}")
        self.assertEqual(response.status_code, 400)
