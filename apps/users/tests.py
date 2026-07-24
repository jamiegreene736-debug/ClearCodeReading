from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from unittest.mock import Mock, patch
from rest_framework.test import APIClient

from apps.assessments.models import Assessment
from apps.notifications.signals import handle_assessment_status_change
from apps.schools.models import School, SchoolMembership
from apps.users.management.commands.seed_demo_login import Command
from apps.users.models import AuditLog, ChildProfile, ConsentLog, ConsentRecord, CustomUser, GuardianRelationship
from apps.users.portal_views import CreatePortalUserView
from apps.users.serializers import CustomUserSerializer


class UsersTests(SimpleTestCase):
    def test_parent_child_roles_are_available(self):
        self.assertEqual(CustomUser.Role.GUARDIAN, "guardian")
        self.assertEqual(CustomUser.Role.STUDENT, "student")

    def test_consent_status_choices_include_granted_and_revoked(self):
        self.assertIn(GuardianRelationship.ConsentStatus.GRANTED, GuardianRelationship.ConsentStatus.values)
        self.assertIn(ConsentLog.Status.REVOKED, ConsentLog.Status.values)

    def test_user_serializer_keeps_password_write_only(self):
        serializer = CustomUserSerializer()
        self.assertTrue(serializer.fields["password"].write_only)

    def test_demo_assessment_seed_disconnects_notification_signal(self):
        command = Command()
        expected = (object(), object())

        with (
            patch.object(command, "_upsert_demo_assessments", return_value=expected) as upsert,
            patch("apps.users.management.commands.seed_demo_login.post_save") as post_save,
        ):
            post_save.disconnect.return_value = True
            result = command._seed_demo_assessments(child=Mock(), teacher=Mock())

        self.assertEqual(result, expected)
        upsert.assert_called_once()
        post_save.disconnect.assert_called_once_with(
            receiver=handle_assessment_status_change,
            sender=Assessment,
        )
        post_save.connect.assert_called_once_with(
            receiver=handle_assessment_status_change,
            sender=Assessment,
        )

    def test_portal_temporary_password_uses_clear_code_prefix(self):
        password = CreatePortalUserView._temporary_password()

        self.assertTrue(password.startswith("ClearCode-"))
        self.assertTrue(password.endswith("!"))

    def test_active_iep_requires_both_idea_approvals(self):
        child = ChildProfile(
            first_name="Avery",
            iep_status=ChildProfile.IEPStatus.ACTIVE,
            idea_parent_consent_status=ChildProfile.ApprovalStatus.APPROVED,
            iep_team_approval_status=ChildProfile.ApprovalStatus.PENDING,
        )
        self.assertFalse(child.idea_services_authorized)
        child.iep_team_approval_status = ChildProfile.ApprovalStatus.APPROVED
        child.idea_parent_consented_at = timezone.now()
        child.iep_team_approved_at = timezone.now()
        self.assertTrue(child.idea_services_authorized)


class ConsentRecordTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        original = School.auto_create_schema
        School.auto_create_schema = False
        try:
            cls.center = School.objects.create(name="Consent Center", slug="consent-center", schema_name="consent_center")
            cls.other_center = School.objects.create(name="Other Center", slug="other-consent", schema_name="other_consent")
        finally:
            School.auto_create_schema = original
        cls.admin = CustomUser.objects.create_user(
            username="consent-admin",
            email="consent-admin@example.com",
            role=CustomUser.Role.SCHOOL_ADMIN,
        )
        cls.other_admin = CustomUser.objects.create_user(
            username="other-consent-admin",
            email="other-consent-admin@example.com",
            role=CustomUser.Role.SCHOOL_ADMIN,
        )
        SchoolMembership.objects.create(school=cls.center, user=cls.admin, role=SchoolMembership.Role.ADMIN)
        SchoolMembership.objects.create(school=cls.other_center, user=cls.other_admin, role=SchoolMembership.Role.ADMIN)
        cls.child = ChildProfile.objects.create(
            first_name="Authorized",
            school=cls.center,
            iep_status=ChildProfile.IEPStatus.ACTIVE,
            idea_parent_consent_status=ChildProfile.ApprovalStatus.APPROVED,
            idea_parent_consented_at=timezone.now(),
            iep_team_approval_status=ChildProfile.ApprovalStatus.APPROVED,
            iep_team_approved_at=timezone.now(),
        )

    def setUp(self):
        self.client = APIClient()

    def test_legacy_fields_remain_fallback_without_formal_record(self):
        self.assertTrue(self.child.idea_services_authorized)

    def test_latest_formal_record_controls_idea_authorization(self):
        ConsentRecord.objects.create(
            child=self.child,
            center=self.center,
            consent_type=ConsentRecord.ConsentType.IDEA_IEP,
            status=ConsentRecord.Status.GRANTED,
            granted_by=self.admin,
            created_by=self.admin,
        )
        self.assertTrue(self.child.idea_services_authorized)

        revoked = ConsentRecord.objects.create(
            child=self.child,
            center=self.center,
            consent_type=ConsentRecord.ConsentType.IDEA_IEP,
            status=ConsentRecord.Status.REVOKED,
            created_by=self.admin,
        )

        self.assertEqual(revoked.version, 2)
        self.assertFalse(self.child.idea_services_authorized)

    def test_expired_grant_blocks_authorization(self):
        ConsentRecord.objects.create(
            child=self.child,
            center=self.center,
            consent_type=ConsentRecord.ConsentType.IDEA_IEP,
            status=ConsentRecord.Status.GRANTED,
            granted_at=timezone.now() - timezone.timedelta(days=2),
            expires_at=timezone.now() - timezone.timedelta(days=1),
            created_by=self.admin,
        )

        self.assertFalse(self.child.idea_services_authorized)

    def test_consent_records_are_append_only(self):
        record = ConsentRecord.objects.create(
            child=self.child,
            center=self.center,
            consent_type=ConsentRecord.ConsentType.IDEA_IEP,
            status=ConsentRecord.Status.PENDING,
            created_by=self.admin,
        )
        record.status = ConsentRecord.Status.GRANTED

        with self.assertRaises(ValueError):
            record.save()

    def test_api_is_center_scoped_and_audited(self):
        self.client.force_authenticate(self.other_admin)
        denied = self.client.post(
            "/api/v1/consent-records/",
            {
                "child_id": self.child.id,
                "consent_type": ConsentRecord.ConsentType.IDEA_IEP,
                "status": ConsentRecord.Status.REVOKED,
            },
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/consent-records/",
            {
                "child_id": self.child.id,
                "consent_type": ConsentRecord.ConsentType.IDEA_IEP,
                "status": ConsentRecord.Status.GRANTED,
                "evidence_notes": "Guardian authorization recorded.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(AuditLog.objects.filter(action="consent_record.created", actor=self.admin).exists())
        detail = self.client.get(f"/api/v1/consent-records/{response.data['id']}/")
        self.assertEqual(detail.status_code, 200, detail.data)

        self.client.force_authenticate(self.other_admin)
        listing = self.client.get("/api/v1/consent-records/")
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual(listing.data["results"], [])
