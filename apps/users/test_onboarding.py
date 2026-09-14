import base64
import re
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.crm_email.google import ProviderError
from apps.crm_email.models import Mailbox, Message
from apps.users.invitations import deliver_invitation
from apps.users.models import CustomUser, UserInvitation


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    USER_INVITATIONS_ALLOW_TEST_EMAIL=True,
    CRM_EMAIL_ENABLED=False,
    PUBLIC_APP_URL="https://testserver",
)
class UserOnboardingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_user(
            username="owner",
            email="owner@example.com",
            role="super_admin",
            password="Owner-Password-123!",
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def invite(self, role="crm_user", email="new@example.com"):
        response = self.client.post(
            reverse("manage_users"),
            {"first_name": "New", "email": email, "role": role},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        return CustomUser.objects.get(email=email)

    def setup_path(self):
        return re.search(
            r"https://testserver(/account/setup/\S+)", mail.outbox[-1].body
        ).group(1)

    def test_create_sends_details_and_private_setup_link_without_password(self):
        user = self.invite()
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("new@example.com", body)
        self.assertIn("Backend employee", body)
        self.assertIn("https://testserver/login/", body)
        self.assertNotIn("Temporary password", body)
        self.assertEqual(user.invitation.status, "sent")
        self.assertEqual(Message.objects.count(), 0)

    def test_setup_link_is_single_use_and_sets_password(self):
        user = self.invite()
        path = self.setup_path()
        self.client.logout()
        response = self.client.get(path)
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            response.url,
            {
                "new_password1": "Choose-A-Password-749!",
                "new_password2": "Choose-A-Password-749!",
            },
        )
        self.assertRedirects(response, reverse("login"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("Choose-A-Password-749!"))
        self.assertIsNotNone(user.invitation.accepted_at)
        self.assertContains(self.client.get(path), "no longer available")

    def test_weak_setup_password_is_rejected(self):
        self.invite()
        self.client.logout()
        target = self.client.get(self.setup_path()).url
        response = self.client.post(
            target, {"new_password1": "password", "new_password2": "password"}
        )
        self.assertContains(response, "too common")
        self.assertIsNone(UserInvitation.objects.get().accepted_at)

    @override_settings(PASSWORD_RESET_TIMEOUT=1)
    def test_expired_link_is_rejected(self):
        self.invite()
        self.client.logout()
        with patch(
            "apps.users.invitations.invitation_tokens._now",
            return_value=timezone.now().replace(tzinfo=None) + timedelta(seconds=5),
        ):
            self.assertContains(
                self.client.get(self.setup_path()), "no longer available"
            )

    def test_resend_revokes_old_link_and_in_browser_session(self):
        user = self.invite()
        old_path = self.setup_path()
        self.client.logout()
        browser_path = self.client.get(old_path).url
        self.client.force_login(self.admin)
        UserInvitation.objects.filter(user=user).update(
            attempted_at=timezone.now() - timedelta(minutes=3)
        )
        self.client.post(reverse("resend_user_invitation", args=[user.invitation.pk]))
        new_path = self.setup_path()
        self.assertNotEqual(new_path, old_path)
        self.client.logout()
        self.assertContains(self.client.get(old_path), "no longer available")
        self.assertContains(self.client.get(browser_path), "no longer available")
        self.assertEqual(self.client.get(new_path).status_code, 302)

    def test_inactive_and_deleted_accounts_cannot_accept(self):
        user = self.invite()
        path = self.setup_path()
        self.client.logout()
        for field in ("is_active", "is_deleted"):
            CustomUser.objects.filter(pk=user.pk).update(
                is_active=field != "is_active", is_deleted=field == "is_deleted"
            )
            self.assertContains(self.client.get(path), "no longer available")

    def test_duplicate_case_insensitive_email_is_rejected(self):
        self.invite()
        response = self.client.post(
            reverse("manage_users"),
            {"first_name": "Again", "email": "NEW@EXAMPLE.COM", "role": "crm_user"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 1)

    def test_roles_cannot_be_escalated(self):
        response = self.client.post(
            reverse("manage_users"),
            {
                "first_name": "Again",
                "email": "admin2@example.com",
                "role": "super_admin",
                "is_superuser": "1",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CustomUser.objects.filter(email="admin2@example.com").exists())

    def test_non_admin_cannot_create_or_resend(self):
        invited = self.invite()
        for role in ("crm_user", "teacher", "guardian"):
            user = CustomUser.objects.create_user(
                username=role, email=f"{role}@example.com", role=role
            )
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("manage_users")).status_code, 403)
            self.assertEqual(
                self.client.post(
                    reverse("resend_user_invitation", args=[invited.invitation.pk])
                ).status_code,
                403,
            )

    def test_school_admin_cannot_invite_employee_or_resend_other_admin_invite(self):
        invited = self.invite()
        school = CustomUser.objects.create_user(
            username="school", email="school@example.com", role="school_admin"
        )
        self.client.force_login(school)
        response = self.client.post(
            reverse("manage_users"),
            {
                "first_name": "Employee",
                "email": "employee@example.com",
                "role": "crm_user",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.client.post(
                reverse("resend_user_invitation", args=[invited.invitation.pk])
            ).status_code,
            403,
        )
        self.assertNotContains(
            self.client.get(reverse("manage_users")), "new@example.com"
        )
        self.invite("teacher", "teacher2@example.com")

    def test_first_login_prompts_employee_and_honors_safe_next(self):
        user = self.invite()
        user.set_password("Test-Password-456!")
        user.save()
        self.client.logout()
        response = self.client.post(
            reverse("login") + "?next=/crm/team/",
            {"username": user.email, "password": "Test-Password-456!"},
        )
        self.assertRedirects(response, reverse("gmail_welcome"))
        self.assertRedirects(self.client.post(reverse("gmail_welcome")), "/crm/team/")
        self.client.logout()
        response = self.client.post(
            reverse("login"), {"username": user.email, "password": "Test-Password-456!"}
        )
        self.assertEqual(response.url, reverse("portal_dashboard"))

    def test_teacher_parent_and_connected_employee_skip_prompt(self):
        for role in ("teacher", "guardian", "crm_user"):
            user = self.invite(role, f"skip-{role}@example.com")
            user.set_password("Test-Password-456!")
            user.save()
            if role == "crm_user":
                Mailbox.objects.create(user=user, status="connected")
            self.client.logout()
            response = self.client.post(
                reverse("login"),
                {"username": user.email, "password": "Test-Password-456!"},
            )
            self.assertNotEqual(response.url, reverse("gmail_welcome"))
            self.client.force_login(self.admin)

    def test_welcome_available_when_google_not_configured(self):
        user = self.invite()
        self.client.force_login(user)
        response = self.client.get(reverse("gmail_welcome"))
        self.assertContains(response, "finish Google setup")
        self.assertContains(response, "Continue to workspace")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        USER_INVITATIONS_ALLOW_TEST_EMAIL=False,
    )
    def test_console_backend_is_failure_not_false_success(self):
        user = self.invite()
        self.assertEqual(user.invitation.status, "failed")
        self.assertIsNone(user.invitation.sent_at)

    @patch("apps.users.invitations.send_mail", side_effect=OSError("private error"))
    def test_ambiguous_delivery_is_visible_and_not_automatically_retried(self, send):
        user = self.invite()
        self.assertEqual(user.invitation.status, "uncertain")
        self.assertNotIn("private error", user.invitation.error)
        deliver_invitation(user.invitation.pk, self.admin)
        self.assertEqual(send.call_count, 1)

    @override_settings(CRM_EMAIL_ENABLED=True)
    @patch("apps.users.invitations.require_configured")
    @patch("apps.users.invitations.Gmail")
    def test_google_delivery_is_private_and_uses_creator_mailbox(
        self, gmail, configured
    ):
        Mailbox.objects.create(
            user=self.admin,
            email=self.admin.email,
            status="connected",
            encrypted_refresh_token="test",
        )
        gmail.return_value.request.return_value = {"id": "message-1"}
        user = self.invite()
        self.assertEqual(user.invitation.status, "sent")
        raw = gmail.return_value.request.call_args.kwargs["json"]["raw"]
        decoded = base64.urlsafe_b64decode(raw).decode()
        self.assertIn("To: new@example.com", decoded)
        self.assertIn("From: owner@example.com", decoded)
        self.assertEqual(Message.objects.count(), 0)

    @override_settings(CRM_EMAIL_ENABLED=True)
    @patch("apps.users.invitations._send", side_effect=ProviderError())
    def test_google_timeout_is_uncertain(self, send):
        self.assertEqual(self.invite().invitation.status, "uncertain")

    def test_resend_is_post_only_and_cooldown_blocks_double_click(self):
        user = self.invite()
        path = reverse("resend_user_invitation", args=[user.invitation.pk])
        self.assertEqual(self.client.get(path).status_code, 405)
        self.client.post(path)
        self.assertEqual(len(mail.outbox), 1)

    def test_legacy_portal_form_also_sends_invitation(self):
        response = self.client.post(
            reverse("portal_create_user"),
            {
                "first_name": "Parent",
                "email": "parent@example.com",
                "role": "guardian",
                "password": "Ignored-Password-123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(
            CustomUser.objects.get(email="parent@example.com").has_usable_password()
        )

    def test_resend_revokes_token_already_open_in_another_browser(self):
        user = self.invite()
        recipient = Client()
        target = recipient.get(self.setup_path()).url
        UserInvitation.objects.filter(user=user).update(
            attempted_at=timezone.now() - timedelta(minutes=3)
        )
        self.client.post(reverse("resend_user_invitation", args=[user.invitation.pk]))
        response = recipient.post(
            target,
            {
                "new_password1": "Test-Password-456!",
                "new_password2": "Test-Password-456!",
            },
        )
        self.assertContains(response, "no longer available")
        user.refresh_from_db()
        self.assertFalse(user.has_usable_password())

    def test_menu_exposes_add_user_and_creation_requires_csrf(self):
        response = self.client.get(reverse("manage_users"))
        self.assertContains(response, 'data-testid="add-user-menu-link"')
        self.assertContains(response, "Backend employee")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(
            client.post(
                reverse("manage_users"),
                {
                    "first_name": "No",
                    "email": "blocked@example.com",
                    "role": "crm_user",
                },
            ).status_code,
            403,
        )

    @override_settings(CRM_EMAIL_ENABLED=True)
    @patch("apps.users.onboarding_views.configuration_errors", return_value=[])
    def test_configured_first_login_offers_real_google_connection(self, configured):
        response = self.client.get(reverse("gmail_welcome"))
        self.assertContains(response, reverse("crm_email_connect"))
        self.assertContains(response, "Connect Gmail")

    def test_interrupted_send_can_be_recovered_explicitly(self):
        user = self.invite()
        UserInvitation.objects.filter(user=user).update(
            status="sending", attempted_at=timezone.now() - timedelta(minutes=3)
        )
        self.client.post(reverse("resend_user_invitation", args=[user.invitation.pk]))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(UserInvitation.objects.get(user=user).status, "sent")
