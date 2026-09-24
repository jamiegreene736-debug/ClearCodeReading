from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.crm.models import FormSubmission, Lead
from apps.crm.services import LeadIntake, record_form_submission
from apps.crm.website_emails import receipt_context
from apps.crm_email import automated
from apps.crm_email.models import AutomatedEmail, Mailbox, StageEmailPilot
from apps.crm_email.stage_emails import render_copy
from apps.crm_email.stage_views import sample_deal
from apps.users.models import AuditLog, CustomUser


@override_settings(PUBLIC_APP_URL="https://example.com")
class AutomatedEmailTests(TestCase):
    def setUp(self) -> None:
        self.admin = CustomUser.objects.create_user(
            username="admin",
            email="admin@clearcodereading.com",
            password="test-password",
            role=CustomUser.Role.SUPER_ADMIN,
        )
        self.staff = CustomUser.objects.create_user(
            username="staff",
            email="staff@clearcodereading.com",
            password="test-password",
            role=CustomUser.Role.CRM_USER,
        )
        self.client.force_login(self.admin)

    def submission(self, kind: str = "consultation") -> FormSubmission:
        return record_form_submission(  # type: ignore[no-untyped-call,no-any-return]
            intake=LeadIntake(
                contact_email="parent@example.com",
                contact_name="Jordan Rivera",
                school_name="Family",
                audience="parent",
            ),
            form_type=kind,
            source_path="/contact/",
            submitted_data={"name": "Jordan Rivera", "email": "parent@example.com"},
        )[1]

    def test_registry_covers_every_automated_email_with_valid_defaults(self) -> None:
        keys = set(automated.specs())
        for expected in (
            "website_consultation",
            "website_consultation_booked",
            "website_survey",
            "website_career",
            "website_newsletter",
            "website_resources",
            "website_support",
            "website_website",
            "website_team",
            "inventory_invitation",
            "inventory_reminder",
            "inventory_follow_up_support",
            "inventory_follow_up_resources",
            "inventory_follow_up_other",
            "inventory_owner_review",
            "inventory_booking_parent",
            "inventory_booking_host",
            "stage_family_enrollment",
            "stage_equity_investment",
            "account_invitation",
        ):
            self.assertIn(expected, keys)
        for spec in automated.specs().values():
            with self.subTest(key=spec.key):
                self.assertIn("subject", spec.fields)
                self.assertIn("body", spec.fields)
                for name in spec.fields:
                    for token in automated.tokens(spec.defaults[name]):
                        self.assertIn(token, spec.placeholders)
                    self.assertIn(name, automated.FIELD_LABELS)

    def test_settings_page_lists_automated_emails_for_administrators_only(self) -> None:
        response = self.client.get(reverse("crm_email_settings"))
        self.assertContains(response, "Automated emails")
        self.assertContains(response, "Consultation request")
        self.assertContains(response, "Account invitation")
        self.assertContains(
            response, reverse("crm_email_automated", args=["website_survey"])
        )
        self.client.force_login(self.staff)
        response = self.client.get(reverse("crm_email_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Automated emails")
        for method in (self.client.get, self.client.post):
            response = method(reverse("crm_email_automated", args=["website_survey"]))
            self.assertEqual(response.status_code, 403)

    def test_unknown_key_is_not_found(self) -> None:
        response = self.client.get(reverse("crm_email_automated", args=["nope"]))
        self.assertEqual(response.status_code, 404)

    def test_editor_shows_defaults_placeholders_and_preview(self) -> None:
        response = self.client.get(
            reverse("crm_email_automated", args=["website_team"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertContains(response, "{{form}}")
        self.assertContains(response, "New consultation request · #1042")
        self.assertContains(response, "Using the default wording")
        self.assertNotContains(response, "Restore default")

    def test_save_applies_to_next_send_and_restore_returns_default(self) -> None:
        url = reverse("crm_email_automated", args=["website_consultation"])
        response = self.client.post(
            url,
            {
                "action": "save",
                "subject": "Hi {{name}}, we got your request",
                "heading": "We’ll be in touch",
                "body": "Thanks {{name}}. Reference {{reference}}.",
                "next_step": "",
                "action_label": "",
                "action_url": "/how-it-works/",
            },
        )
        self.assertRedirects(response, reverse("crm_email_settings"))
        row = AutomatedEmail.objects.get(key="website_consultation")
        self.assertEqual(row.updated_by, self.admin)
        self.assertTrue(
            AuditLog.objects.filter(
                action="crm.automated_email.saved", entity_id="website_consultation"
            ).exists()
        )
        context = receipt_context(self.submission(), team=False)
        self.assertEqual(context["subject"], "Hi Jordan Rivera, we got your request")
        self.assertEqual(context["heading"], "We’ll be in touch")
        self.assertIn("Reference ", str(context["introduction"]))
        self.assertEqual(context["next_step"], "")
        self.assertEqual(context["action_label"], "")
        response = self.client.get(reverse("crm_email_settings"))
        self.assertContains(response, "Customized")
        response = self.client.post(url, {"action": "restore"})
        self.assertRedirects(response, reverse("crm_email_settings"))
        self.assertFalse(
            AutomatedEmail.objects.filter(key="website_consultation").exists()
        )
        context = receipt_context(self.submission(), team=False)
        self.assertEqual(
            context["subject"], "Consultation request received | ClearCode Reading"
        )
        self.assertEqual(context["action_label"], "Explore ClearCode Reading")

    def test_unknown_placeholders_and_multiline_subjects_are_rejected(self) -> None:
        url = reverse("crm_email_automated", args=["website_team"])
        response = self.client.post(
            url,
            {
                "action": "save",
                "subject": "New {{form}}\nline two",
                "heading": "New {{form}}",
                "body": "Visitor {{secret}} submitted a form.",
                "next_step": "",
                "action_label": "Review",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Use a single line.")
        self.assertContains(response, "Unknown placeholder: {{secret}}")
        self.assertFalse(AutomatedEmail.objects.exists())

    def test_team_notice_and_survey_follow_up_use_editable_copy(self) -> None:
        AutomatedEmail.objects.create(
            key="website_team",
            subject="[CRM] {{form}} from {{name}} <{{email}}> #{{reference}}",
            heading="New {{form}}",
            body="Check the CRM.",
            next_step="Reply to {{email}}.",
            action_label="Open",
        )
        submission = self.submission()
        context = receipt_context(submission, team=True)
        self.assertEqual(
            context["subject"],
            f"[CRM] consultation request from Jordan Rivera <parent@example.com> #{submission.pk}",
        )
        self.assertEqual(context["next_step"], "Reply to parent@example.com.")
        self.assertTrue(str(context["action_url"]).startswith("https://example.com/"))
        survey = record_form_submission(  # type: ignore[no-untyped-call]
            intake=LeadIntake(
                contact_email="s@example.com",
                contact_name="Survey",
                school_name="Family",
                audience="parent",
            ),
            form_type="survey",
            source_path="/survey/",
            submitted_data={
                "name": "Survey",
                "email": "s@example.com",
                "engagement_interests": ["priority_waitlist"],
            },
        )[1]
        context = receipt_context(survey, team=False)
        self.assertIn("priority enrollment waitlist", str(context["next_step"]))
        AutomatedEmail.objects.create(
            key="website_survey",
            subject="Survey received",
            heading="Thanks",
            body="Got it.",
            next_step="Before: {{interest_follow_up}} After.",
            action_label="Go",
            action_url="https://example.org/next",
        )
        context = receipt_context(survey, team=False)
        self.assertTrue(str(context["next_step"]).startswith("Before: We’ve recorded"))
        self.assertTrue(str(context["next_step"]).endswith("After."))
        self.assertEqual(context["action_url"], "https://example.org/next")

    def test_first_stage_copy_uses_saved_wording(self) -> None:
        mailbox = Mailbox.objects.create(
            user=self.admin, email=self.admin.email, status="connected"
        )
        pilot = StageEmailPilot.objects.create(
            pk=1, mailbox=mailbox, scheduling_link="https://example.com/book"
        )
        default = render_copy(sample_deal("family_enrollment", pilot), pilot)
        self.assertIn("K–8 students", default.body)
        self.assertIn("1. Families", default.source)
        AutomatedEmail.objects.create(
            key="stage_family_enrollment",
            subject="Hello {{contact.firstname}}",
            body="Book here: {{scheduling_link}}\n\n{{Bethany’s email signature}}",
        )
        edited = render_copy(sample_deal("family_enrollment", pilot), pilot)
        self.assertEqual(edited.subject, "Hello Test")
        self.assertIn("Book here: https://example.com/book", edited.body)
        self.assertIn("bethany@clearcodereading.com", edited.body)
        self.assertEqual(edited.missing, ())
        self.assertEqual(edited.source, "Edited in CRM email settings")
        response = self.client.get(reverse("crm_first_stage_emails"))
        self.assertContains(
            response, reverse("crm_email_automated", args=["stage_family_enrollment"])
        )

    def test_inventory_invitation_defaults_come_from_registry(self) -> None:
        lead = Lead.objects.create(
            contact_name="Jordan Rivera",
            contact_email="jordan@example.com",
            school_name="Family",
        )
        AutomatedEmail.objects.create(
            key="inventory_invitation",
            subject="Inventory for {{parent_name}}",
            body="Hi {{parent_name}}, please complete it.",
            action_label="Start",
        )
        with patch("apps.crm.inventory_views.signing.dumps", return_value="n"):
            response = self.client.get(reverse("inventory_send", args=[lead.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].initial["subject"], "Inventory for Jordan Rivera"
        )
        self.assertEqual(
            response.context["form"].initial["message"],
            "Hi Jordan Rivera, please complete it.",
        )
