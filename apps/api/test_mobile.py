import uuid

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.schools.models import School, SchoolMembership
from apps.users.models import AuditLog, ChildProfile, CustomUser, GuardianRelationship, MobileDevice


class MobileContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        auto_schema = School.auto_create_schema
        School.auto_create_schema = False
        try:
            cls.center = School.objects.create(
                name="Mobile Center",
                slug="mobile-center",
                schema_name="mobile_center",
            )
            cls.other_center = School.objects.create(
                name="Other Center",
                slug="other-mobile-center",
                schema_name="other_mobile_center",
            )
        finally:
            School.auto_create_schema = auto_schema

        cls.specialist = CustomUser.objects.create_user(
            username="mobile-specialist",
            email="mobile-specialist@example.com",
            role=CustomUser.Role.TEACHER,
        )
        SchoolMembership.objects.create(
            school=cls.center,
            user=cls.specialist,
            role=SchoolMembership.Role.SPECIALIST,
        )
        cls.child = ChildProfile.objects.create(
            first_name="Avery",
            last_name="Reader",
            grade_level=ChildProfile.GradeLevel.GRADE_1,
            school=cls.center,
        )
        cls.other_child = ChildProfile.objects.create(
            first_name="Private",
            last_name="Reader",
            grade_level=ChildProfile.GradeLevel.GRADE_2,
            school=cls.other_center,
        )
        cls.guardian = CustomUser.objects.create_user(
            username="mobile-guardian",
            email="mobile-guardian@example.com",
            role=CustomUser.Role.GUARDIAN,
        )
        GuardianRelationship.objects.create(
            guardian=cls.guardian,
            child=cls.child,
            relationship_type=GuardianRelationship.RelationshipType.PARENT,
            consent_status=GuardianRelationship.ConsentStatus.GRANTED,
        )

    def setUp(self):
        self.client = APIClient()

    def test_bootstrap_is_role_and_center_scoped(self):
        self.client.force_authenticate(self.specialist)

        response = self.client.get("/api/v1/mobile/bootstrap/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["user"]["role"], CustomUser.Role.TEACHER)
        self.assertEqual([child["id"] for child in response.data["children"]], [self.child.id])
        self.assertTrue(response.data["capabilities"]["log_sessions"])
        self.assertFalse(response.data["capabilities"]["view_outcomes"])

    def test_guardian_bootstrap_contains_only_authorized_children(self):
        self.client.force_authenticate(self.guardian)

        response = self.client.get("/api/v1/mobile/bootstrap/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([child["id"] for child in response.data["children"]], [self.child.id])
        self.assertTrue(response.data["capabilities"]["view_progress"])
        self.assertFalse(response.data["capabilities"]["log_sessions"])

    def test_device_registration_is_idempotent(self):
        self.client.force_authenticate(self.specialist)
        device_id = uuid.uuid4()
        payload = {
            "device_id": str(device_id),
            "push_token": "sandbox-token",
            "environment": MobileDevice.Environment.SANDBOX,
            "app_version": "1.0",
        }

        first = self.client.post("/api/v1/mobile/devices/", payload, format="json")
        payload["push_token"] = "updated-token"
        second = self.client.post("/api/v1/mobile/devices/", payload, format="json")

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(MobileDevice.objects.filter(user=self.specialist).count(), 1)
        self.assertEqual(MobileDevice.objects.get(user=self.specialist).push_token, "updated-token")

    def test_logout_deactivates_device_and_writes_audit_log(self):
        self.client.force_authenticate(self.specialist)
        device = MobileDevice.objects.create(
            user=self.specialist,
            device_id=uuid.uuid4(),
            last_seen_at=timezone.now(),
        )

        response = self.client.post(
            "/api/v1/mobile/logout/",
            {"device_id": str(device.device_id)},
            format="json",
        )

        self.assertEqual(response.status_code, 204)
        device.refresh_from_db()
        self.assertFalse(device.is_active)
        self.assertTrue(AuditLog.objects.filter(actor=self.specialist, action="mobile.logout").exists())
