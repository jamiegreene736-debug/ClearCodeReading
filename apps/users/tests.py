from django.test import SimpleTestCase
from unittest.mock import Mock, patch

from apps.assessments.models import Assessment
from apps.notifications.signals import handle_assessment_status_change
from apps.users.management.commands.seed_demo_login import Command
from apps.users.models import ConsentLog, CustomUser, GuardianRelationship
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
