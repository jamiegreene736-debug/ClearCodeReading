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
    Message,
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

    def test_notifications_tab_lists_automated_emails_for_administrators_only(
        self,
    ) -> None:
        response = self.client.get(reverse("crm_email_settings"))
        self.assertContains(response, "Email &amp; notification settings")
        self.assertContains(response, reverse("crm_email_notifications"))
        response = self.client.get(reverse("crm_email_notifications"))
        self.assertContains(response, "Email notifications")
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
        self.assertNotContains(response, reverse("crm_email_notifications"))
        self.assertEqual(
            self.client.get(reverse("crm_email_notifications")).status_code, 403
        )
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
        self.assertRedirects(response, reverse("crm_email_notifications"))
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
        response = self.client.get(reverse("crm_email_notifications"))
        self.assertContains(response, "Customized")
        response = self.client.post(url, {"action": "restore"})
        self.assertRedirects(response, reverse("crm_email_notifications"))
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
        self.assertIn("c: (256) 762-8094", edited.body)
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
        self.assertRedirects(response, reverse("crm_email_notifications"))
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

    def test_survey_situation_answer_picks_the_introduction_email(self) -> None:
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
        pilot = StageEmailPilot.objects.create(
            pk=1,
            mailbox=mailbox,
            enabled=True,
            scheduling_link="https://example.com/book",
        )
        respondent = "parent@example.com"

        def answers(
            situation: str, interests: list[str], email: str = respondent
        ) -> EarlyInterestSurveyAnswers:
            return EarlyInterestSurveyAnswers(
                contact_name="Survey Person",
                contact_email=email,
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

        def submit(
            situation: str, interests: list[str], email: str = respondent
        ) -> list[StageEmailDelivery]:
            Lead.objects.filter(contact_email=email).update(is_deleted=True)
            StageEmailDelivery.objects.all().delete()
            with patch("apps.crm_email.stage_emails.require_configured"):
                record_early_interest_survey(
                    answers=answers(situation, interests, email), source=source
                )
                enqueue_stage_emails()
            return list(StageEmailDelivery.objects.order_by("pk"))

        # A real respondent gets the introduction at their own address with the
        # real subject: this is the one live customer delivery. A parent who joins
        # the waitlist leaves the first stage immediately, but the introduction
        # belongs to the contact and still goes out.
        deliveries = submit("prek_2_struggling", ["priority_waitlist", "refer_family"])
        self.assertEqual(
            [d.template_key for d in deliveries], ["survey_family_enrollment"]
        )
        self.assertIsNone(deliveries[0].deal)
        self.assertFalse(deliveries[0].cancelled)
        self.assertEqual(deliveries[0].lead.contact_email, respondent)
        message = deliveries[0].message
        assert message is not None
        self.assertEqual(message.to, [respondent])
        self.assertEqual(message.cc, [])
        self.assertEqual(message.bcc, [])
        self.assertTrue(message.subject.startswith("Thanks for reaching out"))
        self.assertNotIn("[TEST]", message.subject)
        self.assertEqual(message.status, "queued")
        self.assertIn("<p>Hi Survey,</p>", message.body_html)
        self.assertEqual(message.lead_id, deliveries[0].lead_id)
        self.assertEqual(message.mailbox_id, mailbox.pk)

        # The worker's final check accepts the live survey message.
        from apps.crm_email.stage_emails import stage_send_allowed

        self.assertTrue(stage_send_allowed(message))
        # ...but not one whose recipient was tampered with.
        message.to = ["someone-else@example.com"]
        message.save(update_fields=["to"])
        self.assertFalse(stage_send_allowed(message))

        # "Interested for the future or on behalf of another family" is a parent answer.
        deliveries = submit("older_than_grade_8", ["donor"])
        self.assertEqual(
            [d.template_key for d in deliveries], ["survey_family_enrollment"]
        )

        # The community answer gets the general introduction even when the
        # engagement boxes create no deal at all.
        deliveries = submit("community_supporter", ["career_interest"])
        self.assertEqual([d.template_key for d in deliveries], ["survey_general"])
        assert deliveries[0].message is not None
        self.assertEqual(
            deliveries[0].message.subject, "Thanks for connecting with ClearCode"
        )
        self.assertEqual(deliveries[0].message.to, [respondent])
        self.assertIn("Hi Survey,", deliveries[0].message.body_text)
        self.assertIn("ClearCode Foundation", deliveries[0].message.body_text)
        self.assertNotIn("{{", deliveries[0].message.body_text)
        self.assertNotIn("<p>", deliveries[0].message.body_text)

        # Several partner interests still mean one email. With the internal test
        # address the deals also capture first-stage tests, which are cancelled.
        AutomatedEmail.objects.create(
            key="survey_general",
            subject="Welcome partner {{contact.firstname}}",
            body="<p>Thanks for your interest.</p><p>{{scheduling_link}}</p>",
        )
        deliveries = submit(
            "community_supporter", ["donor", "referral_partner"], TEST_RECIPIENT
        )
        self.assertEqual(len(deliveries), 3)
        self.assertEqual(
            [d.template_key for d in deliveries if not d.cancelled], ["survey_general"]
        )
        self.assertTrue(
            all(
                d.error == "Covered by the survey introduction email."
                for d in deliveries
                if d.cancelled
            )
        )
        sent = [d.message for d in deliveries if d.message]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].to, [TEST_RECIPIENT])
        self.assertEqual(sent[0].subject, "Welcome partner Survey")
        self.assertIn(
            '<a href="https://example.com/book">https://example.com/book</a>',
            sent[0].body_html,
        )
        self.assertIn("https://example.com/book", sent[0].body_text)
        self.assertNotIn("<p>", sent[0].body_text)

        # A real respondent with partner interests gets one email and no deal tests.
        deliveries = submit("community_supporter", ["donor", "referral_partner"])
        self.assertEqual([d.template_key for d in deliveries], ["survey_general"])
        self.assertFalse(deliveries[0].cancelled)
        assert deliveries[0].message is not None
        self.assertEqual(deliveries[0].message.to, [respondent])

        # A second survey from the same contact does not send the introduction again.
        with patch("apps.crm_email.stage_emails.require_configured"):
            record_early_interest_survey(
                answers=answers("community_supporter", ["general_email"]), source=source
            )
            enqueue_stage_emails()
        self.assertEqual(
            StageEmailDelivery.objects.filter(
                cancelled=False, deal__isnull=True
            ).count(),
            1,
        )
        self.assertEqual(
            Message.objects.filter(
                stage_delivery__isnull=False, to=[respondent]
            ).count(),
            1,
        )

        # Three different respondents each get their own introduction.
        StageEmailDelivery.objects.all().delete()
        for email in ("one@example.com", "two@example.com", "three@example.com"):
            with patch("apps.crm_email.stage_emails.require_configured"):
                record_early_interest_survey(
                    answers=answers("grade_3_5_struggling", ["consultation"], email),
                    source=source,
                )
        with patch("apps.crm_email.stage_emails.require_configured"):
            enqueue_stage_emails()
        self.assertEqual(
            sorted(
                m.to[0]
                for m in Message.objects.filter(stage_delivery__isnull=False)
                if m.to[0].endswith("@example.com") and m.to[0] != respondent
            ),
            ["one@example.com", "three@example.com", "two@example.com"],
        )

        # Nothing is queued while automated emails are paused.
        pilot.enabled = False
        pilot.save()
        deliveries = submit("prek_2_struggling", ["consultation"], "paused@example.com")
        self.assertEqual(deliveries, [])
        pilot.enabled = True
        pilot.save()

        # A survey with no usable contact email records nothing.
        StageEmailDelivery.objects.all().delete()
        with patch("apps.crm_email.stage_emails.require_configured"):
            record_early_interest_survey(
                answers=answers(
                    "prek_2_struggling", ["consultation"], "blank@example.com"
                ),
                source=source,
            )
        Lead.objects.filter(contact_email="blank@example.com").update(
            contact_email="   "
        )
        with patch("apps.crm_email.stage_emails.require_configured"):
            enqueue_stage_emails()
        delivery = StageEmailDelivery.objects.get()
        self.assertTrue(delivery.cancelled)
        self.assertIsNone(delivery.message)

        response = self.client.get(reverse("crm_email_notifications"))
        self.assertContains(response, "Pipeline introduction emails")
        self.assertContains(response, "Community introduction")
        response = self.client.get(reverse("crm_first_stage_emails"))
        self.assertContains(response, "Survey introduction")
        self.assertContains(response, "Survey Person")


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
        response = self.client.get(reverse("crm_newsletter_list"))
        self.assertContains(response, "New newsletter")
        self.assertContains(response, "Active subscribers")
        self.assertContains(response, '<div class="stat-value">2</div>')
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
        self.assertEqual(
            mail.outbox[-1].from_email, "ClearCode Reading <hello@clearcodereading.com>"
        )
        self.assertEqual(mail.outbox[-1].reply_to, ["hello@clearcodereading.com"])
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
        for name in ("crm_newsletter_new", "crm_newsletter_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_workspace_nav_lists_subscribers_and_contact_card_adds_them(self) -> None:
        response = self.client.get(reverse("crm_newsletter_list"))
        self.assertContains(response, ">Newsletter</a>")
        self.assertContains(response, "Campaigns")
        self.assertContains(response, "Subscribers")
        lead = Lead.objects.create(
            contact_name="Avery Reader",
            contact_email="avery@example.com",
            school_name="Home",
        )
        detail = self.client.get(reverse("crm_contact_detail", args=[lead.pk]))
        self.assertContains(detail, "Add to newsletter")
        denied = self.client.post(
            reverse("crm_contact_newsletter", args=[lead.pk]),
            {"action": "subscribe"},
        )
        self.assertRedirects(denied, reverse("crm_contact_detail", args=[lead.pk]))
        self.assertFalse(
            NewsletterSubscription.objects.filter(email="avery@example.com").exists()
        )
        added = self.client.post(
            reverse("crm_contact_newsletter", args=[lead.pk]),
            {"action": "subscribe", "consent": "yes"},
        )
        self.assertRedirects(added, reverse("crm_contact_detail", args=[lead.pk]))
        subscription = NewsletterSubscription.objects.get(email="avery@example.com")
        self.assertEqual(subscription.lead, lead)
        self.assertEqual(subscription.status, NewsletterSubscription.Status.ACTIVE)
        listed = self.client.get(reverse("crm_newsletter_list") + "?tab=subscribers")
        self.assertContains(listed, "avery@example.com")
        self.assertContains(listed, "Avery Reader")
        removed = self.client.post(
            reverse("crm_contact_newsletter", args=[lead.pk]),
            {"action": "unsubscribe"},
        )
        self.assertRedirects(removed, reverse("crm_contact_detail", args=[lead.pk]))
        subscription.refresh_from_db()
        self.assertEqual(
            subscription.status, NewsletterSubscription.Status.UNSUBSCRIBED
        )

    def test_duplicate_starts_a_new_draft(self) -> None:
        campaign = NewsletterCampaign.objects.create(
            subject="March notes",
            body="Hello",
            body_html="<p>Hello</p>",
            status=NewsletterCampaign.Status.SENT,
            created_by=self.admin,
        )
        response = self.client.post(
            reverse("crm_newsletter_duplicate", args=[campaign.pk])
        )
        copy = NewsletterCampaign.objects.exclude(pk=campaign.pk).get()
        self.assertRedirects(response, reverse("crm_newsletter", args=[copy.pk]))
        self.assertEqual(copy.subject, "Copy of March notes")
        self.assertEqual(copy.status, NewsletterCampaign.Status.DRAFT)
        self.assertEqual(copy.body_html, "<p>Hello</p>")
