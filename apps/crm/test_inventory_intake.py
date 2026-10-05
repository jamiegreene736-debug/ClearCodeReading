import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.core.captcha import CAPTCHA_FIELD, CAPTCHA_MESSAGE, CAPTCHA_MOCK_TOKEN
from apps.crm.inventory import definition
from apps.crm.inventory_models import (
    InventoryChild,
    InventoryInvitation,
    InventoryShareLink,
)
from apps.crm.models import CrmActivity, Lead


@override_settings(PUBLIC_APP_URL="https://example.com")
class InventoryIntakeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = get_user_model().objects.create_user(
            username="intake-staff", role="crm_user"
        )
        self.client.force_login(self.staff)
        self.client.post(reverse("inventory_link"))
        self.link = InventoryShareLink.objects.get()
        self.url = reverse("inventory_intake", args=[self.link.token])
        self.public = Client()

    def payload(self):
        page = self.public.get(self.url)
        return {
            "first_name": "Alex",
            "last_name": "Smith",
            "email": "Alex@Example.com",
            "child_name": "Avery",
            "age": 7,
            "grade": "grade_1",
            "nonce": page.context["form"].initial["nonce"],
        }

    def test_link_is_staff_only_reusable_and_copyable(self):
        self.assertEqual(self.public.post(reverse("inventory_link")).status_code, 302)
        self.client.post(reverse("inventory_link"))
        self.assertEqual(InventoryShareLink.objects.count(), 1)
        self.assertContains(
            self.client.get(reverse("inventory_link")), "https://example.com" + self.url
        )
        self.assertContains(
            self.client.get(reverse("inventory_list")), "Create inventory link"
        )
        user = get_user_model().objects.create_user(
            username="family", email="family@example.com", role="parent"
        )
        self.public.force_login(user)
        self.assertEqual(self.public.post(reverse("inventory_link")).status_code, 403)

    def test_required_fields_and_invalid_grade_do_not_create_records(self):
        for field in ("first_name", "last_name", "email", "child_name", "age", "grade"):
            data = self.payload()
            data[field] = ""
            self.assertEqual(self.public.post(self.url, data).status_code, 200)
        data = self.payload()
        data.update(grade="unknown", age=-1, email="invalid")
        self.public.post(self.url, data)
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(InventoryChild.objects.exists())

    def test_intake_and_duplicate_post_are_atomic_and_idempotent(self):
        data = self.payload()
        response = self.public.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.public.post(self.url, data).url, response.url)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(InventoryInvitation.objects.count(), 1)
        invitation = InventoryInvitation.objects.get()
        self.assertEqual(invitation.child.name, "Avery")
        self.assertEqual(invitation.child.age, 7)
        self.assertEqual(invitation.child.parent.contact_name, "Alex Smith")
        self.assertEqual(invitation.recipient, "alex@example.com")
        self.assertIsNotNone(invitation.started_at)
        self.assertContains(self.public.get(response.url), "Avery")

    def test_email_match_preserves_contact_and_does_not_expose_previous_inventory(self):
        parent = Lead.objects.create(
            contact_name="Staff name",
            contact_email="ALEX@example.com",
            school_name="School",
            status="qualified",
            priority="hot",
            assigned_to=self.staff,
        )
        first = self.public.post(self.url, self.payload())
        second = self.public.post(self.url, self.payload())
        self.assertNotEqual(first.url, second.url)
        self.assertEqual(Lead.objects.count(), 1)
        parent.refresh_from_db()
        self.assertEqual(
            (parent.contact_name, parent.status, parent.priority, parent.school_name),
            ("Staff name", "qualified", "hot", "School"),
        )
        self.assertEqual(parent.assigned_to, self.staff)
        self.assertEqual(parent.inventory_children.count(), 2)
        self.assertNotContains(self.public.get(self.url), "Staff name")

    def test_intake_requires_a_captcha_token(self):
        blocked = self.public.post(self.url, {**self.payload(), CAPTCHA_FIELD: ""})
        self.assertEqual(blocked.status_code, 400)
        self.assertContains(blocked, CAPTCHA_MESSAGE, status_code=400)
        self.assertFalse(Lead.objects.exists())

        accepted = self.public.post(
            self.url,
            {
                **self.payload(),
                "email": "captcha-ok@example.com",
                CAPTCHA_FIELD: CAPTCHA_MOCK_TOKEN,
            },
        )
        self.assertEqual(accepted.status_code, 302)
        self.assertTrue(
            Lead.objects.filter(contact_email="captcha-ok@example.com").exists()
        )

    def test_tamper_spam_rate_limit_and_csrf(self):
        data = self.payload()
        data["nonce"] += "x"
        self.assertEqual(self.public.post(self.url, data).status_code, 400)
        data = self.payload()
        data["website"] = "spam"
        self.assertEqual(self.public.post(self.url, data).status_code, 400)
        self.assertEqual(
            Client(enforce_csrf_checks=True).post(self.url, self.payload()).status_code,
            403,
        )
        self.assertEqual(
            self.public.get(
                reverse("inventory_intake", args=[uuid.uuid4()])
            ).status_code,
            404,
        )
        cache.set("inventory-intake:consultation-booking:127.0.0.1", 20, 3600)
        self.assertEqual(self.public.post(self.url, self.payload()).status_code, 429)
        self.assertFalse(Lead.objects.exists())

    @patch("apps.crm.inventory_views.deliver_pending")
    def test_completed_inventory_is_attached_to_contact_and_review_queue(self, deliver):
        url = self.public.post(self.url, self.payload()).url
        invitation = InventoryInvitation.objects.get()
        for group in definition("grade_1")["groups"]:
            invitation.refresh_from_db()
            data = {q["id"]: "yes" for q in group["questions"]}
            data.update(revision=invitation.revision, action="continue")
            self.public.post(url, data)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.completed_at)
        self.assertTrue(
            CrmActivity.objects.filter(
                lead=invitation.child.parent,
                activity_type="task",
                subject__contains="Avery",
            ).exists()
        )
        self.assertContains(
            self.client.get(reverse("inventory_list") + "?queue=review"), "Avery"
        )
        self.assertContains(
            self.client.get(reverse("inventory_detail", args=[invitation.pk])), "Age 7"
        )

    @patch(
        "apps.crm.inventory_intake.log_activity",
        side_effect=RuntimeError("storage failed"),
    )
    def test_failed_intake_rolls_back(self, log):
        with self.assertRaises(RuntimeError):
            self.public.post(self.url, self.payload())
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(InventoryInvitation.objects.exists())
