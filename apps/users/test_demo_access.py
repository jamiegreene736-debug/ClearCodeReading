from io import StringIO

from django.contrib.auth import authenticate
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.crm_email.security import configuration_errors
from apps.users.models import AuditLog, CustomUser


@override_settings(ENABLE_DEMO_ACCESS=False)
class ProductionDemoAccessTests(TestCase):
    def test_public_login_does_not_offer_demo_access(self) -> None:
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Demo Administrator")
        self.assertNotContains(response, "ClearCodeDemo!2026")
        for role in ("admin", "parent", "teacher"):
            self.assertEqual(
                self.client.post(reverse("demo_login", args=[role])).status_code,
                404,
            )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_seed_commands_cannot_reset_credentials(self) -> None:
        user = CustomUser.objects.create_user(
            username="existing", email="admin@clearcodereading.com", password="private"
        )
        original_hash = user.password
        for command in ("seed_demo_login", "seed_admin_demo_data"):
            with self.assertRaises(CommandError):
                call_command(command, stdout=StringIO())
        user.refresh_from_db()
        self.assertEqual(user.password, original_hash)

    def test_retirement_invalidates_demo_sessions_preserves_private_users(self) -> None:
        demo = CustomUser.objects.create_user(
            username="demo-admin",
            email="admin@clearcodereading.com",
            password="public-password",
            is_superuser=True,
            metadata={"demo": True},
        )
        private = CustomUser.objects.create_user(
            username="private", email="private@clearcodereading.com", password="private"
        )
        self.client.force_login(demo)
        call_command("retire_demo_accounts", stdout=StringIO())
        demo.refresh_from_db()
        private.refresh_from_db()
        self.assertFalse(demo.is_active)
        self.assertFalse(demo.has_usable_password())
        self.assertTrue(private.is_active)
        self.assertTrue(private.check_password("private"))
        self.assertIsNone(authenticate(username=demo.email, password="public-password"))
        self.assertEqual(self.client.get("/crm/email/").status_code, 302)
        call_command("retire_demo_accounts", stdout=StringIO())
        self.assertEqual(
            AuditLog.objects.filter(action="security.demo_accounts.retired").count(), 1
        )
        self.assertTrue(CustomUser.objects.filter(pk=demo.pk).exists())

    @override_settings(ENABLE_DEMO_ACCESS=True)
    def test_isolated_demo_opt_in_remains_available(self) -> None:
        demo = CustomUser.objects.create_user(
            username="demo", email="admin@clearcodereading.com"
        )
        self.assertContains(self.client.get(reverse("login")), "Demo Administrator")
        response = self.client.post(reverse("demo_login", args=["admin"]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(demo.pk))
        with self.assertRaises(CommandError):
            call_command("retire_demo_accounts", stdout=StringIO())
        self.assertIn(
            "Public demo access must be disabled before connecting email",
            configuration_errors(),
        )
