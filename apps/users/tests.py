from django.test import SimpleTestCase

from apps.users.models import ConsentLog, CustomUser, GuardianRelationship
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
