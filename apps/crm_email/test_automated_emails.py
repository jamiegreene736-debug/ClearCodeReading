from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.crm.models import (
    FormSubmission,
    Lead,
    NewsletterCampaign,
    NewsletterSubscription,
)
from apps.crm.services import LeadIntake, record_form_submission
from apps.crm.website_emails import receipt_context
from apps.crm_email import automated
from apps.crm_email.models import (
    AutomatedEmail,
    AutomatedEmailImage,
    Mailbox,
    StageEmailDelivery,
    StageEmailPilot,
)
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
            response, reverse("crm_email_automated", args=["website_support"])
        )
        self.assertNotContains(
            response, reverse("crm_email_automated", args=["website_survey"])
        )
        self.client.force_login(self.staff)
        response = self.client.get(reverse("crm_email_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Automated emails")
        for method in (self.client.get, self.client.post):
            response = method(reverse("crm_email_automated", args=["website_support"]))
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

    def test_team_notice_uses_editable_copy(self) -> None:
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


@override_settings(
    PUBLIC_APP_URL="https://example.com", CRM_EMAIL_DOMAIN="clearcodereading.com"
)
class RichAutomatedEmailTests(TestCase):
    def setUp(self) -> None:
        self.admin = CustomUser.objects.create_user(
            username="admin2",
            email="admin2@clearcodereading.com",
            password="test-password",
            role=CustomUser.Role.SUPER_ADMIN,
        )
        self.client.force_login(self.admin)

    def submission(self) -> FormSubmission:
        return record_form_submission(  # type: ignore[no-untyped-call,no-any-return]
            intake=LeadIntake(
                contact_email="parent@example.com",
                contact_name="Jordan Rivera",
                school_name="Family",
                audience="parent",
            ),
            form_type="consultation",
            source_path="/contact/",
            submitted_data={"name": "Jordan Rivera", "email": "parent@example.com"},
        )[1]

    def test_rich_html_is_sanitized_and_rendered_in_both_parts(self) -> None:
        response = self.client.post(
            reverse("crm_email_automated", args=["website_consultation"]),
            {
                "action": "save",
                "subject": "Hi {{name}}",
                "heading": "Welcome",
                "body": '<h2 style="color:#c53b3b;position:absolute">Hello {{name}}</h2>'
                '<p><img src="https://example.com/crm/email/images/x/" alt="logo" width="200" onerror="alert(1)">'
                '<span style="background-color:#fff3a3;font-size:18px">bright</span></p>'
                '<script>alert(1)</script><a href="javascript:alert(1)">bad</a>',
                "next_step": "<p>We call <b>{{name}}</b> soon.</p>",
                "action_label": "Go",
                "action_url": "/how-it-works/",
            },
        )
        self.assertRedirects(response, reverse("crm_email_settings"))
        row = AutomatedEmail.objects.get(key="website_consultation")
        self.assertIn('style="color:#c53b3b"', row.body)
        self.assertNotIn("position", row.body)
        self.assertNotIn("onerror", row.body)
        self.assertNotIn("<script", row.body)
        self.assertNotIn("javascript:", row.body)
        self.assertIn('<img src="https://example.com/crm/email/images/x/"', row.body)
        self.assertIn("background-color:#fff3a3", row.body)
        context = receipt_context(self.submission(), team=False)
        self.assertIn("Hello Jordan Rivera</h2>", str(context["introduction_html"]))
        self.assertEqual(
            str(context["introduction"]).splitlines()[0], "Hello Jordan Rivera"
        )
        self.assertNotIn("<", str(context["introduction"]))
        self.assertIn("<b>Jordan Rivera</b>", str(context["next_step_html"]))
        self.assertEqual(context["next_step"], "We call Jordan Rivera soon.")

    def test_substituted_values_are_escaped_inside_rich_html(self) -> None:
        AutomatedEmail.objects.create(
            key="website_consultation",
            subject="Hi",
            heading="Welcome",
            body="<p>Hello {{name}}</p>",
            action_url="/how-it-works/",
        )
        submission = record_form_submission(  # type: ignore[no-untyped-call]
            intake=LeadIntake(
                contact_email="x@example.com",
                contact_name="<img src=x onerror=alert(1)>",
                school_name="Family",
                audience="parent",
            ),
            form_type="consultation",
            source_path="/contact/",
            submitted_data={
                "name": "<img src=x onerror=alert(1)>",
                "email": "x@example.com",
            },
        )[1]
        context = receipt_context(submission, team=False)
        self.assertIn(
            "&lt;img src=x onerror=alert(1)&gt;", str(context["introduction_html"])
        )
        self.assertNotIn("<img", str(context["introduction_html"]))

    def test_default_wording_previews_and_edits_as_html(self) -> None:
        response = self.client.get(
            reverse("crm_email_automated", args=["inventory_reminder"])
        )
        self.assertContains(response, "<p>You can complete or continue")
        self.assertContains(response, 'data-rich-editor="automated"')
        self.assertContains(response, "automated-editor-config")
        self.assertContains(response, "automated_editor.js")

    def make_png(self) -> bytes:
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (40, 20), "red").save(buffer, format="PNG")
        return buffer.getvalue()

    def test_image_upload_is_admin_only_validated_and_served_publicly(self) -> None:
        from django.core.files.uploadedfile import SimpleUploadedFile

        url = reverse("crm_email_automated_image_upload")
        response = self.client.post(
            url, {"image": SimpleUploadedFile("logo.png", self.make_png(), "image/png")}
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(
            payload["url"].startswith("https://example.com/crm/email/images/")
        )
        self.assertEqual((payload["width"], payload["height"]), (40, 20))
        image = AutomatedEmailImage.objects.get()
        self.assertEqual(image.uploaded_by, self.admin)
        self.client.logout()
        served = self.client.get(reverse("crm_email_automated_image", args=[image.pk]))
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "image/png")
        self.assertEqual(served.content, self.make_png())
        self.assertIn("immutable", served["Cache-Control"])
        response = self.client.post(
            url, {"image": SimpleUploadedFile("logo.png", self.make_png(), "image/png")}
        )
        self.assertIn(response.status_code, (302, 403))
        staff = CustomUser.objects.create_user(
            username="staff2",
            email="staff2@clearcodereading.com",
            password="test-password",
            role=CustomUser.Role.CRM_USER,
        )
        self.client.force_login(staff)
        response = self.client.post(
            url, {"image": SimpleUploadedFile("logo.png", self.make_png(), "image/png")}
        )
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.post(
            url,
            {"image": SimpleUploadedFile("evil.png", b"<svg onload=1>", "image/png")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(AutomatedEmailImage.objects.count(), 1)

    def test_survey_routes_family_and_one_general_email(self) -> None:
        from apps.crm.surveys import (
            EarlyInterestSurveyAnswers,
            SurveySource,
            record_early_interest_survey,
        )
        from apps.crm_email.stage_emails import TEST_RECIPIENT, enqueue_stage_emails

        mailbox = Mailbox.objects.create(
            user=self.admin,
            email=self.admin.email,
            status="connected",
            encrypted_refresh_token="x",
        )
        StageEmailPilot.objects.create(
            pk=1,
            mailbox=mailbox,
            enabled=True,
            scheduling_link="https://example.com/book",
        )

        def answers(situation: str, interests: list[str]) -> EarlyInterestSurveyAnswers:
            return EarlyInterestSurveyAnswers(
                contact_name="Survey Person",
                contact_email=TEST_RECIPIENT,
                home_zip="32789",
                respondent_situation=situation,
                supports_tried=[],
                annual_reading_spend="",
                commitment_preference="",
                one_to_one_budget="",
                small_group_budget="",
                engagement_interests=interests,
            )

        source = SurveySource(path="/survey/", placement="Main survey page")
        with patch("apps.crm_email.stage_emails.require_configured"):
            record_early_interest_survey(
                answers=answers("prek_2_struggling", ["opening_updates"]), source=source
            )
            family = StageEmailDelivery.objects.get()
            self.assertEqual(family.template_key, "survey_family_enrollment")
            enqueue_stage_emails()
        family.refresh_from_db()
        assert family.message is not None
        self.assertIn("<p>Hi Survey,</p>", family.message.body_html)
        self.assertTrue(
            family.message.subject.startswith("[TEST] Thanks for reaching out")
        )

        Lead.objects.filter(contact_email=TEST_RECIPIENT).update(is_deleted=True)
        StageEmailDelivery.objects.all().delete()
        AutomatedEmail.objects.create(
            key="survey_general",
            subject="Welcome partner {{contact.firstname}}",
            body="<p>Thanks for your interest.</p><p>{{scheduling_link}}</p>",
        )
        with patch("apps.crm_email.stage_emails.require_configured"):
            record_early_interest_survey(
                answers=answers("community_supporter", ["donor", "referral_partner"]),
                source=source,
            )
            deliveries = list(StageEmailDelivery.objects.order_by("pk"))
            self.assertEqual(len(deliveries), 2)
            self.assertEqual(
                sorted(d.template_key for d in deliveries if not d.cancelled),
                ["survey_general"],
            )
            self.assertEqual(sum(1 for d in deliveries if d.cancelled), 1)
            enqueue_stage_emails()
        sent = [d.message for d in StageEmailDelivery.objects.all() if d.message]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].subject, "[TEST] Welcome partner Survey")
        self.assertIn(
            '<a href="https://example.com/book">https://example.com/book</a>',
            sent[0].body_html,
        )
        self.assertIn("https://example.com/book", sent[0].body_text)
        self.assertNotIn("<p>", sent[0].body_text)
        response = self.client.get(reverse("crm_email_settings"))
        self.assertContains(response, "Survey initial emails")
        self.assertContains(response, "Survey: all other pipelines")


def html_part(message: object) -> str:
    alternatives = getattr(message, "alternatives", [])
    return str(alternatives[0][0]) if alternatives else ""


@override_settings(
    PUBLIC_APP_URL="https://example.com",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEBUG=True,
)
class NewsletterCmsTests(TestCase):
    def setUp(self) -> None:
        self.admin = CustomUser.objects.create_user(
            username="admin3",
            email="admin3@clearcodereading.com",
            password="test-password",
            role=CustomUser.Role.SUPER_ADMIN,
        )
        self.client.force_login(self.admin)
        NewsletterSubscription.objects.create(email="one@example.com", name="One")
        NewsletterSubscription.objects.create(email="two@example.com", name="Two")

    def test_settings_lists_newsletters_and_editor_saves_rich_body(self) -> None:
        response = self.client.get(reverse("crm_newsletter_new"))
        self.assertNotContains(response, "This field is required")
        response = self.client.get(reverse("crm_email_settings"))
        self.assertContains(response, "New newsletter")
        self.assertContains(response, "Active subscribers: <strong>2</strong>")
        response = self.client.post(
            reverse("crm_newsletter_new"),
            {
                "action": "save",
                "subject": "October reading tips",
                "preview_text": "Three ideas",
                "body_html": '<h2 style="color:#1a7a7a">Hello families</h2>'
                "<p>Read <b>together</b>.</p><script>x()</script>",
            },
        )
        campaign = NewsletterCampaign.objects.get()
        self.assertRedirects(response, reverse("crm_newsletter", args=[campaign.pk]))
        self.assertEqual(campaign.created_by, self.admin)
        self.assertIn('<h2 style="color:#1a7a7a">', campaign.body_html)
        self.assertNotIn("<script", campaign.body_html)
        self.assertEqual(campaign.body, "Hello families\nRead together.")
        response = self.client.get(reverse("crm_newsletter", args=[campaign.pk]))
        self.assertContains(response, 'data-rich-editor="automated"')
        self.assertContains(response, "Save &amp; review sending")

    def test_review_send_test_then_send_to_subscribers_and_lock(self) -> None:
        from django.core import mail

        campaign = NewsletterCampaign.objects.create(
            subject="Welcome",
            body="plain",
            body_html="<p>Rich <i>body</i></p>",
            created_by=self.admin,
        )
        url = reverse("crm_newsletter_send", args=[campaign.pk])
        response = self.client.get(url)
        self.assertContains(response, "Send to all subscribers now")
        self.assertContains(response, "<strong>2</strong> active subscriber")
        response = self.client.post(url, {"action": "test"})
        self.assertRedirects(response, url)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.admin.email])
        self.assertEqual(mail.outbox[0].subject, "[TEST] Welcome")
        test_html = html_part(mail.outbox[0])
        self.assertIn("Rich <i>body</i>", test_html)
        self.assertIn("test copy", test_html)
        response = self.client.post(url, {"action": "send"})
        self.assertRedirects(response, reverse("crm_newsletter", args=[campaign.pk]))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, NewsletterCampaign.Status.SENT)
        self.assertEqual(campaign.delivered_count, 2)
        self.assertEqual(len(mail.outbox), 3)
        html = html_part(mail.outbox[-1])
        self.assertIn("Rich <i>body</i>", html)
        self.assertIn("/newsletter/unsubscribe/", html)
        self.assertIn("Rich body", str(mail.outbox[-1].body))
        self.assertTrue(AuditLog.objects.filter(action="crm.newsletter.sent").exists())
        response = self.client.post(
            reverse("crm_newsletter", args=[campaign.pk]),
            {"action": "save", "subject": "Changed", "body_html": "<p>x</p>"},
        )
        self.assertEqual(response.status_code, 200)
        campaign.refresh_from_db()
        self.assertEqual(campaign.subject, "Welcome")
        self.assertContains(response, "wording is locked")
        response = self.client.post(
            reverse("crm_newsletter", args=[campaign.pk]), {"action": "delete"}
        )
        self.assertEqual(response.status_code, 403)

    def test_newsletter_pages_are_admin_only(self) -> None:
        staff = CustomUser.objects.create_user(
            username="staff3",
            email="staff3@clearcodereading.com",
            password="test-password",
            role=CustomUser.Role.CRM_USER,
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("crm_email_settings"))
        self.assertNotContains(response, "New newsletter")
        for name in ("crm_newsletter_new",):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
