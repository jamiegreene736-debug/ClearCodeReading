from io import BytesIO
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from apps.api.permissions import has_coppa_consent
from apps.crm.models import Lead
from apps.users.models import (
    ChildProfile,
    ConsentLog,
    CustomUser,
    GuardianRelationship,
    ReaderDetails,
    SignedReaderForms,
)


class ParentPortalTests(TestCase):
    def setUp(self) -> None:
        self.parent = CustomUser.objects.create_user(
            username="parent", email="family@example.com"
        )
        self.client.force_login(self.parent)
        self.url = reverse("portal_add_child")
        self.data = {
            "first_name": "Alex",
            "signed_name": "Sam Reader",
            "electronic_signature": "on",
            "filming": "decline",
            "relationship_type": "legal_guardian",
            "data_processing": "on",
            "submission_id": str(uuid4()),
        }

    def test_retries_siblings_and_separate_family_access(self) -> None:
        other = CustomUser.objects.create_user(
            username="other", email="other@example.com"
        )
        data = {
            **self.data,
            "guardian": other.pk,
            "assessment": "on",
            "school_sharing": "on",
        }
        self.client.post(self.url, data)
        self.client.post(self.url, data)
        relationship = GuardianRelationship.objects.get()
        self.assertEqual(relationship.guardian_id, self.parent.pk)
        self.assertEqual(relationship.relationship_type, "legal_guardian")
        self.assertTrue(relationship.is_primary)
        self.assertTrue(has_coppa_consent(relationship.child))
        self.assertEqual(ConsentLog.objects.count(), 3)
        self.client.post(
            self.url,
            {**self.data, "first_name": "Robin", "submission_id": str(uuid4())},
        )
        self.assertEqual(ChildProfile.objects.count(), 2)
        self.assertEqual(Lead.objects.filter(linked_user=self.parent).count(), 1)
        self.assertFalse(
            has_coppa_consent(ChildProfile.objects.get(first_name="Robin"))
        )
        self.client.force_login(other)
        dashboard = self.client.get(reverse("portal_dashboard"))
        self.assertNotContains(dashboard, "Alex")
        self.assertNotContains(dashboard, "Robin")
        self.assertContains(dashboard, "Add your first child")

    def test_invalid_submission_has_no_side_effects(self) -> None:
        for field, value in (
            ("first_name", " "),
            ("submission_id", ""),
            ("filming", ""),
            ("electronic_signature", ""),
            ("signed_name", " "),
            ("relationship_type", "invalid"),
            ("data_processing", ""),
        ):
            with self.subTest(field=field):
                page = self.client.post(self.url, {**self.data, field: value})
                self.assertIn(field, page.context["form"].errors)
        self.assertFalse(ChildProfile.objects.exists())
        self.assertFalse(GuardianRelationship.objects.exists())
        self.assertFalse(ConsentLog.objects.exists())

    def test_only_signed_in_private_parent_accounts_can_add_children(self) -> None:
        self.client.logout()
        self.assertRedirects(
            self.client.get(self.url), f"{reverse('login')}?next={self.url}"
        )
        for role in (
            "teacher",
            "student",
            "crm_user",
            "super_admin",
            "school_admin",
            "guardian",
        ):
            user = CustomUser.objects.create_user(
                username=role,
                email=f"{role}@example.com",
                role=role,
                metadata={"demo": role == "guardian"},
            )
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        self.assertFalse(ChildProfile.objects.exists())

    def test_signed_copy_and_private_profile(self) -> None:
        signed = self.client.post(self.url, self.data)
        child = ChildProfile.objects.get()
        profile_url = reverse("portal_reader_profile", args=[child.pk])
        photo_url = reverse("portal_reader_photo", args=[child.pk])
        forms_url = reverse("portal_reader_forms", args=[child.pk])
        self.assertRedirects(signed, profile_url)
        profile_form = self.client.get(profile_url).context["form"]
        self.assertFalse(profile_form.is_bound)
        self.assertEqual(profile_form["first_name"].value(), "Alex")
        receipt = SignedReaderForms.objects.get()
        self.assertFalse(receipt.filming_allowed)
        self.assertContains(self.client.get(forms_url), "Permission declined")
        source = BytesIO()
        Image.new("RGB", (40, 40)).save(source, "PNG")
        image = SimpleUploadedFile("portrait.png", source.getvalue(), "image/png")
        data = {
            "first_name": "Alex",
            "last_name": "Reader",
            "date_of_birth": "2018-06-10",
            "grade_level": "grade_2",
            "school_name": "Lake School",
            "activities": "Sports",
            "favorites": "Hello Kitty",
            "photo": image,
        }
        self.assertRedirects(
            self.client.post(profile_url, data), reverse("portal_dashboard")
        )
        self.assertContains(self.client.get(profile_url), "Hello Kitty")
        self.assertEqual(self.client.get(photo_url)["Content-Type"], "image/jpeg")
        self.assertEqual(ReaderDetails.objects.get().school_name, "Lake School")
        self.assertContains(
            self.client.post(
                profile_url, {"first_name": "Alex", "date_of_birth": "2999-01-01"}
            ),
            "earlier birthday",
        )
        other = CustomUser.objects.create_user(
            username="other", email="other@example.com"
        )
        self.client.force_login(other)
        for url in (profile_url, photo_url, forms_url):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.post(profile_url, {"first_name": "Someone else"}).status_code,
            404,
        )

    def test_existing_reader_must_sign_before_opening_profile(self) -> None:
        child = ChildProfile.objects.create(first_name="Existing")
        GuardianRelationship.objects.create(
            guardian=self.parent,
            child=child,
            relationship_type="parent",
            is_primary=True,
        )
        profile_url = reverse("portal_reader_profile", args=[child.pk])
        forms_url = reverse("portal_reader_forms", args=[child.pk])
        self.assertRedirects(
            self.client.post(profile_url, {"first_name": "Edit"}), forms_url
        )
        self.assertRedirects(
            self.client.post(forms_url, {**self.data, "filming": "allow"}), profile_url
        )
        self.assertTrue(SignedReaderForms.objects.get().filming_allowed)
        self.assertEqual(ChildProfile.objects.get().first_name, "Existing")

    def test_parent_sees_only_family_navigation_and_cannot_manage_accounts(
        self,
    ) -> None:
        dashboard = self.client.get(reverse("portal_dashboard"))
        self.assertNotContains(dashboard, 'href="/crm/')
        self.assertNotContains(dashboard, 'id="manage-navigation-button"')
        self.assertEqual(self.client.get(reverse("crm_dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("manage_users")).status_code, 403)
        api = APIClient()
        api.force_authenticate(self.parent)
        self.assertEqual(
            api.patch(
                reverse("api:users:user-detail", args=[self.parent.pk]),
                {"role": "crm_user"},
            ).status_code,
            403,
        )
        self.assertEqual(
            api.post(reverse("api:users:guardian-relationship-list"), {}).status_code,
            403,
        )

    def test_registration_requires_account_owner_to_add_to_existing_family(
        self,
    ) -> None:
        self.client.logout()
        payload = {
            "parent": {"email": self.parent.email},
            "child": {"first_name": "Alex"},
            "relationship_type": "parent",
            "consents": [{"consent_type": "data_processing"}],
        }
        url = reverse("api:users:user-register-parent-child")
        self.assertEqual(
            self.client.post(url, payload, content_type="application/json").status_code,
            400,
        )
        self.assertFalse(ChildProfile.objects.exists())
        payload["parent"] = {"email": "signup@example.com", "first_name": "Taylor"}
        self.assertEqual(
            self.client.post(url, payload, content_type="application/json").status_code,
            201,
        )
        self.assertEqual(Lead.objects.get().linked_user.email, "signup@example.com")

    def test_public_registration_leaves_teacher_assignment_to_staff(self) -> None:
        self.client.logout()
        payload = {
            "parent": {"email": "newfamily@example.com"},
            "child": {
                "first_name": "Alex",
                "learning_profile": {"assigned_teacher_id": 42},
            },
            "relationship_type": "parent",
            "consents": [{"consent_type": "data_processing"}],
        }
        response = self.client.post(
            reverse("api:users:user-register-parent-child"),
            payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ChildProfile.objects.exists())
