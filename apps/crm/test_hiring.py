from datetime import date, timedelta
from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import RecruitingInterest
from apps.crm.hiring import (
    CHECKLISTS,
    business_due_date,
    initialize_candidate,
)
from apps.crm.hiring_models import HiringCandidate
from apps.crm.models import Lead
from apps.users.models import CustomUser


class HiringTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bethany = CustomUser.objects.create_user(
            username="bethany",
            email="bethany@example.com",
            role="crm_user",
            hiring_enabled=True,
        )
        cls.brook = CustomUser.objects.create_user(
            username="brook",
            email="brook@example.com",
            role="crm_user",
            hiring_enabled=True,
        )
        cls.crm_user = CustomUser.objects.create_user(
            username="crm", email="crm@example.com", role="crm_user"
        )
        cls.admin = CustomUser.objects.create_superuser(
            username="hiring-admin",
            email="hiring-admin@example.com",
            password="test-pass",
        )
        cls.application = RecruitingInterest.objects.create(
            name="Test Teacher",
            email="teacher@example.com",
            career_path="teacher",
            role_interest="Teacher",
            notes="Application",
            owner=cls.bethany,
            resume_data=b"private resume",
            resume_original_name="resume.pdf",
        )
        cls.candidate = cls.application.hiring

    def setUp(self):
        self.client.force_login(self.bethany)
        self.candidate.refresh_from_db()

    def payload(self, **changes):
        candidate = HiringCandidate.objects.get(pk=self.candidate.pk)
        fields = [
            "stage",
            "next_action",
            "due_date",
            "blocker",
            "checklist",
            "evaluation_notes",
            "decision",
            "decision_notes",
            "offer_sent_on",
            "offer_terms",
            "offer_response",
            "offer_responded_on",
            "onboarding_notes",
            "outcome_reason",
            "review_date",
            "revision",
        ]
        result = {
            key: getattr(candidate, key) if getattr(candidate, key) is not None else ""
            for key in fields
        }
        result.update(changes)
        return result

    def update(self, **changes):
        return self.client.post(
            reverse("crm_hiring_update", args=[self.candidate.pk]),
            self.payload(**changes),
        )

    def test_new_teacher_application_enrolls_once_without_sales_lead(self):
        initialize_candidate(self.application)
        self.assertEqual(
            HiringCandidate.objects.filter(application=self.application).count(), 1
        )
        self.assertEqual(self.candidate.events.count(), 1)
        self.assertFalse(Lead.objects.exists())
        self.assertEqual(self.candidate.due_date, business_due_date())

    def test_company_applicant_is_not_enrolled(self):
        application = RecruitingInterest.objects.create(
            name="Company Applicant",
            email="company@example.com",
            career_path="company",
            role_interest="Operations",
            notes="Test",
        )
        self.assertFalse(
            HiringCandidate.objects.filter(application=application).exists()
        )

    def test_workspace_renders_my_candidates_and_private_documents(self):
        response = self.client.get(reverse("crm_hiring"))
        self.assertContains(response, "Test Teacher")
        self.assertContains(response, "Save candidate updates")
        self.assertContains(response, "Pending intake")
        self.assertContains(response, "pending intake")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertNotContains(response, "private resume")

    def test_pending_intake_count_includes_the_whole_team(self):
        RecruitingInterest.objects.create(
            name="New Applicant",
            email="new-applicant@example.com",
            career_path="teacher",
            role_interest="Teacher",
            notes="Application",
            owner=self.brook,
        )
        mine = self.client.get(reverse("crm_hiring"))
        self.assertContains(mine, "Pending intake")
        self.assertContains(mine, ">2<")
        self.assertNotContains(mine, "New Applicant")
        intake = self.client.get(
            reverse("crm_hiring"), {"owner": "all", "stage": "application"}
        )
        self.assertContains(intake, "New Applicant")
        self.assertContains(intake, "Test Teacher")
        dashboard = self.client.get(reverse("crm_dashboard"))
        self.assertContains(dashboard, "Teachers pending intake")
        self.assertContains(dashboard, "New Applicant")

    def test_private_access_requires_explicit_hiring_access(self):
        for user in [
            self.crm_user,
            CustomUser.objects.create_user(
                username="teacher",
                email="t@example.com",
                role="teacher",
                hiring_enabled=True,
            ),
        ]:
            self.client.force_login(user)
            for url in [
                reverse("crm_hiring"),
                reverse("crm_hiring_document", args=[self.candidate.pk, "resume"]),
            ]:
                self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("crm_hiring")).status_code, 302)

    def test_owner_changes_next_action_and_history(self):
        response = self.update(
            next_action="Call candidate", due_date=timezone.localdate()
        )
        self.assertEqual(response.status_code, 302)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.next_action, "Call candidate")
        self.assertEqual(self.candidate.revision, 1)
        self.assertEqual(self.candidate.events.first().actor, self.bethany)

    def test_other_owner_cannot_change_decision(self):
        self.client.force_login(self.brook)
        self.assertEqual(
            self.update(decision="hire", decision_notes="Qualified").status_code, 403
        )
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.decision, "pending")

    def test_transfer_is_complete_and_old_owner_cannot_edit(self):
        response = self.client.post(
            reverse("crm_hiring_assign", args=[self.candidate.pk]),
            {"owner": self.brook.pk, "revision": 0},
        )
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.owner, self.brook)
        self.assertEqual(self.update(next_action="Old owner edit").status_code, 403)
        self.client.force_login(self.brook)
        self.assertEqual(
            self.update(next_action="Brook's next action").status_code, 302
        )

    def test_ineligible_owner_is_rejected(self):
        response = self.client.post(
            reverse("crm_hiring_assign", args=[self.candidate.pk]),
            {"owner": self.crm_user.pk, "revision": 0},
        )
        self.assertEqual(response.status_code, 400)
        self.application.refresh_from_db()
        self.assertEqual(self.application.owner, self.bethany)

    def test_stale_updates_do_not_overwrite(self):
        stale = self.payload(next_action="Stale change")
        self.update(next_action="Latest change")
        response = self.client.post(
            reverse("crm_hiring_update", args=[self.candidate.pk]), stale
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "changed in another session", status_code=400)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.next_action, "Latest change")

    def test_stale_owner_transfer_is_rejected(self):
        self.update(next_action="Updated")
        response = self.client.post(
            reverse("crm_hiring_assign", args=[self.candidate.pk]),
            {"owner": self.brook.pk, "revision": 0},
        )
        self.assertEqual(response.status_code, 400)

    def test_active_candidate_requires_action_and_date(self):
        self.assertEqual(self.update(next_action=" ", due_date="").status_code, 400)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.next_action, "Review application")

    def test_ready_requires_completed_hiring_evidence(self):
        self.assertEqual(self.update(stage="ready").status_code, 400)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.stage, "application")

    def test_same_owner_completes_entire_hiring_flow(self):
        checklist = []
        for stage, completed in [
            ("screening", "application"),
            ("interview", "screening"),
            ("decision", "interview"),
        ]:
            checklist += [key for key, _ in CHECKLISTS[completed]]
            response = self.update(
                stage=stage,
                checklist=checklist,
                evaluation_notes="Strong teaching demonstration",
            )
            self.assertEqual(response.status_code, 302, response.content.decode()[:500])
        today = timezone.localdate()
        self.assertEqual(
            self.update(
                stage="offer",
                decision="hire",
                decision_notes="Meets role requirements",
                offer_sent_on=today,
                offer_terms="Agreed teaching role and rate",
            ).status_code,
            302,
        )
        self.assertEqual(
            self.update(
                stage="onboarding", offer_response="accepted", offer_responded_on=today
            ).status_code,
            302,
        )
        checklist += [key for key, _ in CHECKLISTS["onboarding"]]
        self.assertEqual(
            self.update(
                stage="ready", checklist=checklist, next_action="", due_date=""
            ).status_code,
            302,
        )
        self.candidate.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual(self.candidate.stage, "ready")
        self.assertEqual(self.application.owner, self.bethany)
        self.assertEqual(self.application.status, "closed")

    def test_hold_requires_reason_and_future_review_and_uses_review_due(self):
        self.assertEqual(self.update(stage="hold").status_code, 400)
        review = timezone.localdate() + timedelta(days=7)
        self.assertEqual(
            self.update(
                stage="hold",
                outcome_reason="Candidate unavailable until next week",
                review_date=review,
            ).status_code,
            302,
        )
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.due_date, review)

    def test_closed_outcomes_require_reason(self):
        for stage in ["withdrawn", "not_selected"]:
            self.assertEqual(
                self.update(stage=stage, outcome_reason="").status_code, 400
            )
            self.assertEqual(
                self.update(
                    stage=stage,
                    outcome_reason="Candidate declined the role",
                    next_action="",
                    due_date="",
                ).status_code,
                302,
            )

    def test_invalid_stage_and_checklist_are_rejected(self):
        self.assertEqual(self.update(stage="invented").status_code, 400)
        self.assertEqual(self.update(checklist=["invented"]).status_code, 400)

    def test_offer_dates_cannot_be_future_or_reversed(self):
        today = timezone.localdate()
        self.assertEqual(
            self.update(offer_sent_on=today + timedelta(days=1)).status_code, 400
        )
        self.assertEqual(
            self.update(
                offer_sent_on=today, offer_responded_on=today - timedelta(days=1)
            ).status_code,
            400,
        )

    def test_overdue_and_owner_filters_and_default_order(self):
        other = RecruitingInterest.objects.create(
            name="Brook Candidate",
            email="brook-candidate@example.com",
            career_path="teacher",
            role_interest="Teacher",
            notes="Application",
            owner=self.brook,
        )
        HiringCandidate.objects.filter(pk=other.hiring.pk).update(
            due_date=timezone.localdate() - timedelta(days=1)
        )
        self.assertNotContains(
            self.client.get(reverse("crm_hiring")), "Brook Candidate"
        )
        response = self.client.get(
            reverse("crm_hiring"), {"owner": "all", "attention": "1"}
        )
        self.assertContains(response, "Brook Candidate")
        self.assertEqual(list(response.context["page_obj"])[0].pk, other.hiring.pk)

    def test_inactive_owner_is_in_needs_owner_queue(self):
        CustomUser.objects.filter(pk=self.bethany.pk).update(is_active=False)
        self.client.force_login(self.brook)
        response = self.client.get(reverse("crm_hiring"), {"owner": "unassigned"})
        self.assertContains(response, "Test Teacher")

    def test_documents_are_downloaded_privately(self):
        response = self.client.get(
            reverse("crm_hiring_document", args=[self.candidate.pk, "resume"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"private resume")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(
            self.client.get(
                reverse("crm_hiring_document", args=[self.candidate.pk, "invalid"])
            ).status_code,
            404,
        )

    def test_only_admin_can_grant_hiring_access(self):
        url = reverse("crm_hiring_access", args=[self.crm_user.pk])
        self.assertEqual(self.client.post(url, {"enabled": "1"}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url, {"enabled": "1"}).status_code, 302)
        self.crm_user.refresh_from_db()
        self.assertTrue(self.crm_user.has_hiring_access)

    def test_confirmed_contact_import_is_idempotent_and_preserves_lead(self):
        lead = Lead.objects.create(
            contact_name="Interested Teacher",
            contact_email="interested@example.com",
            school_name="Teacher",
            audience="referral_partners",
        )
        url = reverse("crm_hiring_from_contact", args=[lead.pk])
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(HiringCandidate.objects.filter(lead=lead).count(), 1)
        lead.refresh_from_db()
        self.assertEqual(lead.status, "new")

    def test_contact_with_existing_application_links_without_duplicate(self):
        lead = Lead.objects.create(
            contact_name="Test Teacher",
            contact_email=self.application.email,
            school_name="Teacher",
        )
        self.client.post(reverse("crm_hiring_from_contact", args=[lead.pk]))
        self.assertEqual(HiringCandidate.objects.count(), 1)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.lead, lead)

    def test_csrf_required_and_get_does_not_mutate(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.bethany)
        url = reverse("crm_hiring_update", args=[self.candidate.pk])
        self.assertEqual(client.post(url, self.payload()).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_business_day_due_date_skips_weekend(self):
        self.assertEqual(business_due_date(date(2026, 9, 11)), date(2026, 9, 15))

    def test_backfill_preserves_closed_unknown_outcome(self):
        # Invoke the data migration twice to verify legacy import is retry-safe.
        application = RecruitingInterest.objects.create(
            name="Legacy",
            email="legacy@example.com",
            career_path="teacher",
            role_interest="Teacher",
            notes="Historic",
            owner=self.crm_user,
            status="closed",
        )
        application.hiring.delete()
        migration = import_module("apps.crm.migrations.0010_backfill_teacher_hiring")
        with connection.schema_editor() as editor:
            migration.backfill(apps, editor)
            migration.backfill(apps, editor)
        candidate = HiringCandidate.objects.get(application=application)
        self.assertEqual(candidate.stage, "hold")
        self.assertIn("confirm", candidate.outcome_reason)
        self.assertEqual(candidate.events.count(), 1)
        self.crm_user.refresh_from_db()
        self.assertTrue(self.crm_user.hiring_enabled)

    def test_malformed_candidate_ids_do_not_raise_server_errors(self):
        for value in ["²", "9" * 5000, "9223372036854775808", "-1"]:
            self.assertEqual(
                self.client.get(
                    reverse("crm_hiring"), {"candidate": value}
                ).status_code,
                404,
            )

    def test_new_application_prefers_designated_hiring_owners(self):
        from apps.crm.hiring import select_intake_owner

        self.assertEqual(select_intake_owner(), self.brook)

    def test_new_application_respects_eligible_configured_owner(self):
        from apps.crm.hiring import select_intake_owner

        with self.settings(RECRUITING_OWNER_EMAIL=self.bethany.email):
            self.assertEqual(select_intake_owner(), self.bethany)

    def test_stage_change_suggests_action_and_due_date(self):
        response = self.update(stage="screening", checklist=["application_reviewed"])
        self.assertEqual(response.status_code, 302)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.next_action, "Complete initial screening")
        self.assertEqual(self.candidate.due_date, business_due_date())
