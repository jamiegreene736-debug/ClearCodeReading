from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.crm.contact_lifecycle import set_contact_deleted
from apps.crm.models import CrmActivity, FormSubmission, Lead
from apps.users.models import AuditLog, CustomUser


class ContactLifecycleTests(TestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        self.member = CustomUser.objects.create_user(
            username="member", role=CustomUser.Role.CRM_USER
        )
        self.contact = Lead.objects.create(
            contact_name="Recover Me", contact_email="recover@example.com"
        )
        self.note = CrmActivity.objects.create(
            lead=self.contact, body="Preserve history"
        )
        self.submission = FormSubmission.objects.create(
            lead=self.contact, submitted_data={"answer": "original"}
        )
        self.url = reverse("crm_contact_delete", args=[self.contact.pk])
        self.client.force_login(self.admin)

    def test_confirmed_delete_hides_contact_and_restore_preserves_history(self):
        response = self.client.post(self.url, {"confirm": "delete"})
        self.assertRedirects(response, reverse("crm_contact_list"))
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.is_deleted)
        self.assertIsNotNone(self.contact.deleted_at)
        self.assertEqual(
            self.client.get(
                reverse("crm_contact_detail", args=[self.contact.pk])
            ).status_code,
            404,
        )
        self.assertNotContains(
            self.client.get(reverse("crm_contact_list")), "Recover Me"
        )
        self.assertTrue(CrmActivity.objects.filter(pk=self.note.pk).exists())
        self.assertTrue(FormSubmission.objects.filter(pk=self.submission.pk).exists())
        call_command(
            "restore_crm_contact",
            self.contact.pk,
            actor_id=self.admin.pk,
            stdout=StringIO(),
        )
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.is_deleted)
        self.assertIsNone(self.contact.deleted_at)
        self.assertEqual(
            self.client.get(
                reverse("crm_contact_detail", args=[self.contact.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.contact.pk),
                action__in=["crm.contact.deleted", "crm.contact.restored"],
            ).count(),
            2,
        )

    def test_get_missing_confirmation_and_csrf_do_not_delete(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.post(self.url)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(
            csrf_client.post(self.url, {"confirm": "delete"}).status_code, 403
        )
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.is_deleted)

    def test_non_admin_cannot_delete_through_ui_or_api(self):
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.post(self.url, {"confirm": "delete"}).status_code, 403
        )
        self.assertNotContains(
            self.client.get(reverse("crm_contact_detail", args=[self.contact.pk])),
            'id="delete-contact-dialog"',
        )
        api = APIClient()
        api.force_authenticate(self.member)
        self.assertEqual(
            api.delete(f"/api/v1/leads/{self.contact.pk}/").status_code, 403
        )
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.is_deleted)

    def test_api_admin_delete_is_recoverable(self):
        api = APIClient()
        api.force_authenticate(self.admin)
        self.assertEqual(
            api.delete(f"/api/v1/leads/{self.contact.pk}/").status_code, 204
        )
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.is_deleted)
        self.assertTrue(CrmActivity.objects.filter(pk=self.note.pk).exists())

    def test_audit_failure_rolls_back_delete(self):
        with patch(
            "apps.crm.contact_lifecycle.AuditLog.objects.create",
            side_effect=RuntimeError("audit unavailable"),
        ), self.assertRaises(RuntimeError):
            set_contact_deleted(
                contact_id=self.contact.pk, actor=self.admin, deleted=True
            )
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.is_deleted)

    def test_admin_has_confirmation_dialog_and_repeated_delete_is_idempotent(self):
        self.assertContains(
            self.client.get(reverse("crm_contact_detail", args=[self.contact.pk])),
            'id="delete-contact-dialog"',
        )
        for _ in range(2):
            set_contact_deleted(
                contact_id=self.contact.pk, actor=self.admin, deleted=True
            )
        self.assertEqual(
            AuditLog.objects.filter(
                action="crm.contact.deleted", entity_id=str(self.contact.pk)
            ).count(),
            1,
        )

    def test_deleted_contact_tasks_are_hidden_then_restored(self):
        CrmActivity.objects.create(
            lead=self.contact,
            activity_type="task",
            subject="Follow up",
            due_at=timezone.now(),
        )
        set_contact_deleted(contact_id=self.contact.pk, actor=self.admin, deleted=True)
        response = self.client.get(reverse("crm_dashboard"))
        self.assertEqual(response.context["overdue_tasks"], 0)
        self.assertEqual(response.context["task_rows"], [])
        set_contact_deleted(contact_id=self.contact.pk, actor=self.admin, deleted=False)
        response = self.client.get(reverse("crm_dashboard"))
        self.assertEqual(response.context["overdue_tasks"], 1)

    def test_restore_rejects_non_admin(self):
        set_contact_deleted(contact_id=self.contact.pk, actor=self.admin, deleted=True)
        with self.assertRaises(CommandError):
            call_command(
                "restore_crm_contact",
                self.contact.pk,
                actor_id=self.member.pk,
                stdout=StringIO(),
            )
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.is_deleted)
