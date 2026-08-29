from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth.models import Group as AuthGroup
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.assessments.models import (
    Assessment,
    AssessmentQuestion,
    ChildAssessmentResponse,
)
from apps.blog.models import BlogPost
from apps.core.models import RecruitingInterest
from apps.crm.models import (
    Company,
    CrmActivity,
    FormSubmission,
    IntakeTriage,
    Lead,
    NewsletterCampaign,
    NewsletterDelivery,
    NewsletterSubscription,
    Opportunity,
)
from apps.curriculum.models import (
    ChildLessonAssignment,
    Curriculum,
    CurriculumSequence,
    Lesson,
    LessonTemplate,
    PlacementEvidence,
    PlacementRecommendation,
    SequencePlan,
    Skill,
    SkillCrosswalk,
    StudentPlacement,
    StudentPlacementOverride,
    TeacherLessonTemplate,
    TeachingAid,
)
from apps.decision_support.models import (
    Flag,
    GrowthFlag,
    Milestone,
    MilestonePrediction,
    OutcomeAggregate,
    Prediction,
)
from apps.outcomes.models import DeIdentifiedOutcomeSnapshot, build_center_key
from apps.progress.models import MasteryRecord, Progress
from apps.scheduling.models import (
    Group,
    ProviderAvailability,
    ScheduleBooking,
    ScheduleGroupProposal,
    WaitlistEntry,
)
from apps.schools.models import School, SchoolMembership
from apps.sessions.models import (
    Session,
    SessionRevision,
    SessionTemplate,
    SkillObservation,
)
from apps.tenants.models import Domain
from apps.users.management.commands.seed_demo_login import (
    DEMO_ADMIN_EMAIL,
    DEMO_PARENT_EMAIL,
    DEMO_TEACHER_EMAIL,
)
from apps.users.models import AuditLog, ChildProfile, ConsentRecord, CustomUser, Profile
from apps.workforce.models import (
    Agreement,
    ClassificationReview,
    ComplianceTask,
    Credential,
    Engagement,
    PayableItem,
    Payment,
    PaymentRun,
    PayerLegalEntity,
    ProviderEvent,
    ProviderOnboarding,
    RateSchedule,
    SensitiveDataReference,
    TaxYearSummary,
    WorkerAssignment,
    WorkerProfile,
    WorkforceRoleMembership,
)

DEMO_SOURCE = "seed_admin_demo_data"


class Command(BaseCommand):
    help = "Create safe, idempotent samples across every admin-visible section."

    def handle(self, *args, **options):
        call_command("seed_reading_survey_questions", verbosity=0)
        call_command("seed_demo_login", verbosity=0)
        center = self._seed_center()
        with transaction.atomic():
            self._seed_all(center)
        empty = sorted(
            model._meta.label
            for model in admin.site._registry
            if not model._default_manager.exists()
        )
        if empty:
            raise CommandError("Admin demo coverage is incomplete: " + ", ".join(empty))
        self.stdout.write(
            self.style.SUCCESS(
                f"Sample data is ready across all {len(admin.site._registry)} admin-visible record types."
            )
        )

    def _seed_center(self):
        center, _ = School.objects.update_or_create(
            slug="demo-learning-center",
            defaults={
                "schema_name": "demo_learning_center",
                "name": "Demo Learning Center",
                "district": "Sample District",
                "contact_email": DEMO_ADMIN_EMAIL,
                "settings": {"demo": True, "source": DEMO_SOURCE},
                "branding": {"display_name": "Demo Learning Center"},
                "is_deleted": False,
            },
        )
        Domain.objects.update_or_create(
            domain="demo.clearcodereading.local",
            defaults={"tenant": center, "is_primary": False},
        )
        return center

    def _seed_all(self, center):
        now = timezone.now()
        admin_user = CustomUser.objects.get(email=DEMO_ADMIN_EMAIL)
        teacher = CustomUser.objects.get(email=DEMO_TEACHER_EMAIL)
        parent = CustomUser.objects.get(email=DEMO_PARENT_EMAIL)
        child = ChildProfile.objects.get(student_identifier="DEMO-AVERY-READER")
        ChildProfile.objects.filter(pk=child.pk).update(
            school=center,
            availability_windows=[{"day": "Tuesday", "start": "15:30", "end": "17:00"}],
            iep_status=ChildProfile.IEPStatus.ACTIVE,
            idea_parent_consent_status=ChildProfile.ApprovalStatus.APPROVED,
            idea_parent_consented_at=now,
            iep_team_approval_status=ChildProfile.ApprovalStatus.APPROVED,
            iep_team_approved_at=now,
        )
        child.refresh_from_db()
        AuthGroup.objects.get_or_create(name="Demo Content Reviewers")
        for user, role, title in (
            (admin_user, SchoolMembership.Role.OWNER, "Demo Center Owner"),
            (teacher, SchoolMembership.Role.SPECIALIST, "Demo Reading Specialist"),
            (parent, SchoolMembership.Role.VIEWER, "Demo Family Viewer"),
        ):
            SchoolMembership.objects.update_or_create(
                school=center,
                user=user,
                defaults={
                    "role": role,
                    "title": title,
                    "joined_at": now,
                    "is_deleted": False,
                },
            )
            Profile.objects.update_or_create(
                user=user,
                defaults={
                    "display_name": user.get_full_name(),
                    "timezone": "America/New_York",
                    "preferences": {"demo": True},
                    "onboarding_completed_at": now,
                    "is_deleted": False,
                },
            )
        ConsentRecord.objects.get_or_create(
            child=child,
            center=center,
            consent_type=ConsentRecord.ConsentType.IDEA_IEP,
            version=1,
            defaults={
                "status": ConsentRecord.Status.GRANTED,
                "granted_by": parent,
                "granted_at": now,
                "evidence_notes": "Demo consent record for interface review only.",
                "created_by": admin_user,
                "is_deleted": False,
            },
        )
        AuditLog.objects.get_or_create(
            action="demo.sample_data.created",
            entity_type="admin_portal",
            entity_id="demo-learning-center",
            defaults={
                "actor": admin_user,
                "after": {"demo": True},
                "metadata": {"source": DEMO_SOURCE},
            },
        )

        self._seed_marketing(admin_user)
        self._seed_crm(admin_user, center, now)
        curriculum, first, second, skill = self._seed_curriculum(
            admin_user, teacher, child, center
        )
        assessment = Assessment.objects.get(
            child=child, title="Demo Reading Survey - Avery Reader"
        )
        assessment.school, assessment.skill = center, skill
        assessment.save(update_fields=["school", "skill", "updated_at"])
        question = AssessmentQuestion.objects.filter(is_active=True).first()
        option = question.question_options.first()
        ChildAssessmentResponse.objects.update_or_create(
            assessment=assessment,
            child=child,
            question=question,
            defaults={
                "selected_option": option,
                "answer": {"value": option.value if option else "demo-answer"},
                "is_correct": bool(option and option.is_correct),
                "score_value": option.score_value if option else Decimal("1.00"),
                "time_taken": 18,
                "metadata": {"demo": True},
                "is_deleted": False,
            },
        )
        placement = self._seed_placement(
            admin_user, teacher, child, center, curriculum, first, second, assessment
        )
        session = self._seed_sessions(
            admin_user, teacher, child, center, curriculum, first
        )
        self._seed_progress(
            admin_user,
            teacher,
            child,
            center,
            skill,
            placement,
            second,
            session,
            assessment,
        )
        self._seed_scheduling(
            admin_user, teacher, child, center, curriculum, first, second
        )
        self._seed_workforce(admin_user, teacher, center, session)
        self._seed_outcomes(center, curriculum)

    def _seed_marketing(self, admin_user):
        BlogPost.objects.update_or_create(
            slug="demo-reading-routine",
            defaults={
                "title": "Demo: A simple reading routine",
                "excerpt": "A sample article showing how published content appears.",
                "body": "This is sample content for reviewing the ClearCode Reading admin experience.",
                "category": "Family resources",
                "author": admin_user,
                "status": BlogPost.Status.DRAFT,
            },
        )
        RecruitingInterest.objects.update_or_create(
            email="sample.candidate@example.com",
            defaults={
                "name": "Sample Candidate",
                "career_path": RecruitingInterest.CareerPath.TEACHER,
                "role_interest": "Reading Specialist",
                "notes": "Demo application for interface comparison. Not a real applicant.",
                "status": RecruitingInterest.Status.REVIEWING,
                "owner": admin_user,
            },
        )

    def _seed_crm(self, admin_user, center, now):
        company, _ = Company.objects.update_or_create(
            name="Demo Literacy Partners",
            defaults={
                "owner": admin_user,
                "notes": "Sample CRM organization.",
                "metadata": {"demo": True},
            },
        )
        lead, _ = Lead.objects.update_or_create(
            contact_email="sample.family@example.com",
            defaults={
                "school_name": center.name,
                "contact_name": "Sample Family",
                "audience": Lead.Audience.PARENT,
                "organization_name": company.name,
                "company": company,
                "source": Lead.Source.REFERRAL,
                "status": Lead.Status.QUALIFIED,
                "assigned_to": admin_user,
                "estimated_students": 1,
                "notes": "Sample family inquiry for CRM review.",
                "metadata": {"demo": True, "relationship_interests": ["advocate"]},
            },
        )
        FormSubmission.objects.update_or_create(
            lead=lead,
            form_type=FormSubmission.FormType.CONSULTATION,
            source_path="/demo/sample-consultation/",
            defaults={
                "submitted_data": {"demo": True, "preferred_time": "weekday afternoon"}
            },
        )
        CrmActivity.objects.update_or_create(
            lead=lead,
            subject="Demo follow-up call",
            defaults={
                "activity_type": CrmActivity.ActivityType.TASK,
                "body": "Review reading goals and explain the assessment process.",
                "due_at": now + timedelta(days=2),
                "created_by": admin_user,
                "assigned_to": admin_user,
            },
        )
        Opportunity.objects.update_or_create(
            lead=lead,
            pipeline=Opportunity.Pipeline.FAMILY_ENROLLMENT,
            student_name="Avery Reader",
            term_year="2026-2027",
            defaults={
                "company": company,
                "school": center,
                "owner": admin_user,
                "stage": Opportunity.Stage.FAMILY_CONSULTATION,
                "priority": Opportunity.Priority.MEDIUM,
                "funding_type": Opportunity.FundingType.PRIVATE_PAY,
                "grade_band": Opportunity.GradeBand.PREK_2,
                "value": Decimal("1200.00"),
                "probability": 50,
                "expected_close_date": timezone.localdate() + timedelta(days=21),
                "next_steps": "Complete the demo consultation.",
                "metadata": {"demo": True},
            },
        )
        submission, _ = FormSubmission.objects.update_or_create(
            lead=lead,
            form_type=FormSubmission.FormType.WEBSITE,
            source_path="/demo/sample-partner-interest/",
            defaults={
                "submitted_data": {
                    "demo": True,
                    "relationship_interests": ["referral_partner"],
                }
            },
        )
        IntakeTriage.objects.update_or_create(
            submission=submission,
            defaults={
                "lead": lead,
                "source_signal": IntakeTriage.SourceSignal.PARTNER_INTEREST,
                "selected_pipelines": [Opportunity.Pipeline.REFERRAL_PARTNERS],
                "advocate_selected": True,
            },
        )
        subscription, _ = NewsletterSubscription.objects.update_or_create(
            email="sample.reader@example.com",
            defaults={
                "name": "Sample Reader",
                "source_path": "/demo/",
                "status": NewsletterSubscription.Status.ACTIVE,
            },
        )
        campaign, _ = NewsletterCampaign.objects.update_or_create(
            subject="Demo newsletter — reading practice ideas",
            defaults={
                "preview_text": "Sample preview; this draft is never sent automatically.",
                "body": "Demo newsletter content for reviewing the editor and delivery list.",
                "status": NewsletterCampaign.Status.DRAFT,
                "created_by": admin_user,
            },
        )
        NewsletterDelivery.objects.update_or_create(
            campaign=campaign,
            subscription=subscription,
            defaults={
                "recipient_email": subscription.email,
                "status": NewsletterDelivery.Status.PENDING,
                "attempts": 0,
            },
        )

    def _seed_curriculum(self, admin_user, teacher, child, center):
        skill, _ = Skill.objects.update_or_create(
            code="DEMO-CVC",
            defaults={
                "name": "Demo CVC word decoding",
                "domain": Skill.Domain.PHONICS,
                "grade_band": "Grades 1-2",
                "description": "Sample skill for comparing lesson and progress screens.",
                "metadata": {"demo": True},
                "is_deleted": False,
            },
        )
        curriculum, _ = Curriculum.objects.update_or_create(
            center=center,
            code=Curriculum.Code.PFR,
            version="demo-2026.1",
            defaults={
                "name": "Demo Phonics for Reading",
                "created_by": admin_user,
                "updated_by": admin_user,
                "metadata": {"demo": True},
            },
        )
        positions = []
        for number, title in (
            (1, "Short a word families"),
            (2, "Short i word families"),
        ):
            position, _ = CurriculumSequence.objects.update_or_create(
                curriculum=curriculum,
                code=f"DEMO-PFR-A-{number:02d}",
                defaults={
                    "center": center,
                    "sequence_order": number,
                    "level": CurriculumSequence.PFRLevel.A,
                    "lesson_number": number,
                    "concept_number": None,
                    "title": title,
                    "position_type": CurriculumSequence.PositionType.PHONICS_CONCEPT,
                    "description": "Demo curriculum position.",
                    "created_by": admin_user,
                    "updated_by": admin_user,
                },
            )
            positions.append(position)
        SkillCrosswalk.objects.update_or_create(
            center=center,
            skill_node_a=positions[0],
            skill_node_b=positions[1],
            mapping_type=SkillCrosswalk.MappingType.A_PRECEDES_B,
            version="demo-2026.1",
            defaults={
                "equivalence": Decimal("0.750"),
                "notes": "Demo progression comparison.",
            },
        )
        lesson, _ = Lesson.objects.update_or_create(
            slug="demo-build-and-read-cvc-words",
            defaults={
                "title": "Demo: Build and read CVC words",
                "skill": skill,
                "grade_level": "Grade 2",
                "duration_minutes": 25,
                "objective": "Blend three sounds to read short-vowel words.",
                "content": {
                    "warm_up": "Sound review",
                    "practice": "Word-building ladder",
                    "demo": True,
                },
                "materials": ["letter tiles", "word cards"],
                "differentiation": {
                    "support": "Use two-choice prompts",
                    "extension": "Write a sentence",
                },
                "is_published": False,
                "is_deleted": False,
            },
        )
        TeachingAid.objects.update_or_create(
            lesson=lesson,
            title="Demo CVC word-building cards",
            defaults={
                "skill": skill,
                "aid_type": TeachingAid.AidType.MANIPULATIVE,
                "content": {"words": ["map", "sit", "run"]},
                "metadata": {"demo": True},
            },
        )
        template, _ = LessonTemplate.objects.update_or_create(
            slug="demo-cvc-practice-template",
            defaults={
                "title": "Demo CVC practice template",
                "skill": skill,
                "grade_band": "Grades 1-2",
                "description": "Sample reusable specialist lesson.",
                "goal": "Read 8 of 10 CVC words accurately.",
                "recommended_minutes": 20,
                "activities": [{"name": "Word ladder", "minutes": 10}],
                "materials": ["letter tiles"],
                "is_active": True,
                "is_deleted": False,
            },
        )
        TeacherLessonTemplate.objects.update_or_create(
            teacher=teacher,
            template=template,
            defaults={
                "assigned_by": admin_user,
                "notes": "Demo assignment.",
                "is_deleted": False,
            },
        )
        ChildLessonAssignment.objects.update_or_create(
            child=child,
            template=template,
            status=ChildLessonAssignment.Status.IN_PROGRESS,
            defaults={
                "assigned_by": teacher,
                "due_date": timezone.localdate() + timedelta(days=7),
                "teacher_notes": "Demo lesson assignment.",
                "is_deleted": False,
            },
        )
        return curriculum, positions[0], positions[1], skill

    def _seed_placement(
        self, admin_user, teacher, child, center, curriculum, first, second, assessment
    ):
        evidence, _ = PlacementEvidence.objects.update_or_create(
            center=center,
            child=child,
            assessment_version="demo-2026.1",
            defaults={
                "curriculum": curriculum,
                "source_assessment": assessment,
                "instrument": PlacementEvidence.Instrument.PFR_PLACEMENT,
                "source": PlacementEvidence.Source.READING_SURVEY,
                "status": PlacementEvidence.Status.COMPLETED,
                "administered_by": teacher,
                "raw_results": {
                    "parts": [{"position_code": first.code, "accuracy": 80}],
                    "demo": True,
                },
                "supporting_context": {"note": "Sample evidence"},
                "created_by": admin_user,
                "updated_by": admin_user,
            },
        )
        placement, _ = StudentPlacement.objects.update_or_create(
            child=child,
            is_active=True,
            defaults={
                "center": center,
                "curriculum": curriculum,
                "current_position": first,
                "methodology_rationale": "Demo placement based on sample survey results.",
                "placement_evidence": {"source": DEMO_SOURCE},
                "placed_by": teacher,
                "created_by": admin_user,
                "updated_by": admin_user,
                "is_deleted": False,
            },
        )
        recommendation, _ = PlacementRecommendation.objects.update_or_create(
            evidence=evidence,
            defaults={
                "center": center,
                "recommended_curriculum": curriculum,
                "recommended_position": first,
                "decision": PlacementRecommendation.Decision.PLACE,
                "status": PlacementRecommendation.Status.CONFIRMED,
                "deficit_profile": [{"code": "short_vowel_fluency"}],
                "rule_trace": {"demo": True},
                "rationale": "Start with short-vowel blending before advancing.",
                "final_position": first,
                "final_curriculum": curriculum,
                "confirmed_by": teacher,
                "confirmed_at": timezone.now(),
                "resulting_placement": placement,
                "created_by": admin_user,
                "updated_by": admin_user,
            },
        )
        StudentPlacementOverride.objects.update_or_create(
            placement=placement,
            previous_position=first,
            new_position=second,
            defaults={
                "center": center,
                "rationale": "Demo override showing specialist reasoning.",
                "evidence_considered": {"demo": True},
                "source_recommendation": recommendation,
                "specialist": teacher,
                "created_by": admin_user,
                "updated_by": admin_user,
            },
        )
        SequencePlan.objects.update_or_create(
            placement=placement,
            status=SequencePlan.Status.ACTIVE,
            defaults={
                "center": center,
                "specialist_notes": "Demo two-step sequence plan.",
                "created_by": admin_user,
                "updated_by": admin_user,
                "is_deleted": False,
            },
        )
        return placement

    def _seed_sessions(self, admin_user, teacher, child, center, curriculum, position):
        template, _ = SessionTemplate.objects.update_or_create(
            center=center,
            curriculum=curriculum,
            curriculum_position=position,
            session_part=Session.InterventionPart.PFR_1A,
            version=1,
            defaults={
                "title": "Demo PFR Session 1a",
                "capture_fields": {"required": [], "properties": {}},
                "metadata": {"demo": True},
                "created_by": admin_user,
                "updated_by": admin_user,
            },
        )
        scheduled = timezone.make_aware(datetime(2026, 8, 25, 15, 30))
        session, _ = Session.objects.get_or_create(
            center=center,
            child=child,
            scheduled_start=scheduled,
            defaults={
                "specialist": teacher,
                "curriculum_position": position,
                "session_template": template,
                "status": Session.Status.COMPLETED,
                "intervention_part": Session.InterventionPart.PFR_1A,
                "started_at": scheduled,
                "ended_at": scheduled + timedelta(minutes=45),
                "activities_completed": [
                    {
                        "code": "word_reading",
                        "status": "completed",
                        "minutes": 15,
                        "item_set_id": "demo-set-1",
                    }
                ],
                "item_sets": {
                    "word_reading": {
                        "item_set_id": "demo-set-1",
                        "items": ["map", "sit", "run"],
                    }
                },
                "accuracy_rate": Decimal("80.00"),
                "accuracy_numerator": 8,
                "accuracy_denominator": 10,
                "time_to_mastery_signals": {
                    "cumulative_sessions_at_position": 2,
                    "first_attempt_accuracy": 70,
                    "latest_accuracy": 80,
                    "prompts_per_10_items": 2,
                    "independent_transfer": False,
                    "reteach": False,
                },
                "error_patterns": [],
                "behavioral_observations": [],
                "next_session_direction": "Continue short-vowel contrast practice.",
                "home_practice_suggestion": "Read five demo word cards.",
                "notes": "Sample completed session.",
                "created_by": admin_user,
                "updated_by": teacher,
                "is_deleted": False,
            },
        )
        session.targeted_positions.set([position])
        SessionRevision.objects.update_or_create(
            session=session,
            revision=session.revision,
            defaults={
                "center": center,
                "changed_by": teacher,
                "snapshot": {"demo": True, "status": session.status},
            },
        )
        SkillObservation.objects.update_or_create(
            session=session,
            curriculum_position=position,
            defaults={
                "center": center,
                "child": child,
                "accuracy_rate": Decimal("80.00"),
                "response_rating": 4,
                "source_session_revision": session.revision,
                "metadata": {"demo": True},
                "created_by": admin_user,
                "updated_by": teacher,
            },
        )
        return session

    def _seed_progress(
        self,
        admin_user,
        teacher,
        child,
        center,
        skill,
        placement,
        target,
        session,
        assessment,
    ):
        progress, _ = Progress.objects.update_or_create(
            child=child,
            skill=skill,
            defaults={
                "school": center,
                "status": Progress.Status.DEVELOPING,
                "current_score": Decimal("80.00"),
                "target_score": Decimal("90.00"),
                "attempts": 2,
                "last_assessment": assessment,
                "evidence": [{"source": DEMO_SOURCE}],
                "notes": "Demo progress record.",
                "metadata": {"demo": True},
                "is_deleted": False,
            },
        )
        MasteryRecord.objects.update_or_create(
            child=child,
            skill=skill,
            progress=progress,
            defaults={
                "assessment": assessment,
                "mastered_by": teacher,
                "score": Decimal("92.00"),
                "evidence": [{"source": DEMO_SOURCE}],
                "metadata": {"demo": True},
                "is_deleted": False,
            },
        )
        milestone, _ = Milestone.objects.update_or_create(
            center=center,
            child=child,
            definition="Demo milestone: complete short-vowel word families.",
            defaults={
                "curriculum_position": target,
                "target_date": timezone.localdate() + timedelta(days=30),
                "status": Milestone.Status.IN_PROGRESS,
                "created_by": admin_user,
                "updated_by": teacher,
            },
        )
        Flag.objects.update_or_create(
            center=center,
            child=child,
            code=Flag.Code.FLAT_ACCURACY,
            defaults={
                "trigger_rule": {"minimum_sessions": 3},
                "evidence_snapshot": {"demo": True, "accuracy": [72, 75, 76]},
                "related_session": session,
                "curriculum_position": placement.current_position,
                "routed_to": teacher,
                "model_or_rule_version": "demo-rule-2026.1",
                "created_by": admin_user,
                "updated_by": teacher,
            },
        )
        Prediction.objects.update_or_create(
            center=center,
            child=child,
            target_milestone=milestone,
            defaults={
                "target_position": None,
                "estimated_sessions": 4,
                "estimated_date": None,
                "confidence": Decimal("0.700"),
                "model_version": "demo-model-2026.1",
                "evidence": {"demo": True, "sessions": 2},
                "created_by": admin_user,
                "updated_by": teacher,
            },
        )
        growth, _ = GrowthFlag.objects.update_or_create(
            child=child,
            position=placement.current_position,
            flag_code=GrowthFlag.Code.FLAT_ACCURACY,
            status=GrowthFlag.Status.OPEN,
            defaults={
                "center": center,
                "trigger_session": session,
                "severity": GrowthFlag.Severity.MEDIUM,
                "evidence_snapshot": {"demo": True},
                "explanation": "Sample flag showing a flat accuracy pattern.",
                "advisory_recommendation": "Review error patterns before changing placement.",
                "created_by": admin_user,
                "updated_by": teacher,
            },
        )
        growth.routed_to.set([teacher])
        MilestonePrediction.objects.update_or_create(
            child=child,
            is_current=True,
            defaults={
                "center": center,
                "placement": placement,
                "target_position": target,
                "target_label": "Demo: short-vowel fluency",
                "predicted_sessions": 4,
                "predicted_date": timezone.localdate() + timedelta(days=28),
                "lower_bound_sessions": 3,
                "upper_bound_sessions": 6,
                "confidence": MilestonePrediction.Confidence.MEDIUM,
                "evidence_summary": {"demo": True, "completed_sessions": 2},
                "explanation": "A planning estimate based on sample progress.",
                "parent_timeline": "About four sessions, with a likely range of three to six.",
                "created_by": admin_user,
                "updated_by": teacher,
                "is_deleted": False,
            },
        )
        end = timezone.localdate().replace(day=1) - timedelta(days=1)
        OutcomeAggregate.objects.update_or_create(
            center=center,
            dimension=OutcomeAggregate.Dimension.METHODOLOGY,
            dimension_value=Curriculum.Code.PFR,
            metric_name="demo_session_completion_rate",
            period_start=end.replace(day=1),
            period_end=end,
            defaults={
                "value": Decimal("0.8000"),
                "cohort_size": 5,
                "is_deleted": False,
            },
        )

    def _seed_scheduling(
        self, admin_user, teacher, child, center, curriculum, first, second
    ):
        start = timezone.make_aware(datetime(2026, 9, 8, 15, 30))
        ProviderAvailability.objects.update_or_create(
            center=center,
            specialist=teacher,
            defaults={
                "windows": [{"day": "Tuesday", "start": "15:00", "end": "18:00"}],
                "max_group_size": 4,
                "is_active": True,
            },
        )
        Group.objects.update_or_create(
            center=center,
            name="Demo Short-Vowel Group",
            defaults={
                "curriculum": curriculum,
                "skill_band": "Short vowels",
                "sequence_start": first,
                "sequence_end": second,
                "primary_specialist": teacher,
                "notes": "Sample instructional group.",
            },
        )
        proposal, _ = ScheduleGroupProposal.objects.update_or_create(
            center=center,
            signature="demo-group-proposal-2026-09-08",
            defaults={
                "specialist": teacher,
                "curriculum": curriculum,
                "starts_at": start,
                "ends_at": start + timedelta(minutes=45),
                "score": 88,
                "rationale": "Demo availability and placement match.",
                "created_by": admin_user,
                "metadata": {"demo": True},
            },
        )
        proposal.children.set([child])
        ScheduleBooking.objects.update_or_create(
            center=center,
            child=child,
            starts_at=start,
            defaults={
                "proposal": proposal,
                "specialist": teacher,
                "ends_at": start + timedelta(minutes=45),
                "status": ScheduleBooking.Status.PROPOSED,
                "sync_status": ScheduleBooking.SyncStatus.PENDING,
                "metadata": {"demo": True, "external_sync_disabled": True},
            },
        )
        WaitlistEntry.objects.update_or_create(
            center=center,
            child=child,
            is_active=True,
            defaults={
                "submarket": "Demo virtual cohort",
                "notes": "Sample waitlist entry.",
            },
        )

    def _seed_workforce(self, admin_user, teacher, center, session):
        payer, _ = PayerLegalEntity.objects.update_or_create(
            legal_name="Demo ClearCode Reading Entity",
            defaults={"display_name": "Demo ClearCode", "jurisdiction_state": "FL"},
        )
        WorkforceRoleMembership.objects.update_or_create(
            payer=payer,
            user=admin_user,
            role=WorkforceRoleMembership.Role.WORKFORCE_ADMIN,
            defaults={"is_active": True},
        )
        worker, _ = WorkerProfile.objects.update_or_create(
            user=teacher, defaults={"status": WorkerProfile.Status.CANDIDATE}
        )
        engagement, _ = Engagement.objects.update_or_create(
            payer=payer,
            worker=worker,
            starts_on=date(2026, 9, 1),
            defaults={
                "classification": Engagement.Classification.PENDING,
                "status": Engagement.Status.CLASSIFICATION_PENDING,
                "work_state": "FL",
                "anticipated_calendar_year_compensation": Decimal("5000.00"),
            },
        )
        WorkerAssignment.objects.update_or_create(
            engagement=engagement,
            center=center,
            starts_on=date(2026, 9, 1),
            defaults={"is_active": True},
        )
        ClassificationReview.objects.update_or_create(
            engagement=engagement,
            version=1,
            defaults={
                "decision": ClassificationReview.Decision.NEEDS_REVIEW,
                "rationale": "Demo classification awaiting human review.",
                "evidence": {"demo": True},
                "reviewed_by": admin_user,
            },
        )
        ProviderOnboarding.objects.update_or_create(
            engagement=engagement,
            defaults={
                "provider": "demo-provider",
                "external_onboarding_id": "demo-onboarding-reference",
                "status": ProviderOnboarding.Status.NOT_INVITED,
                "remediation_codes": ["sample_only"],
            },
        )
        SensitiveDataReference.objects.update_or_create(
            engagement=engagement,
            provider="demo-provider",
            defaults={
                "external_subject_id": "demo-subject-reference",
                "data_categories": ["tax_form_status", "payment_profile_status"],
                "status": SensitiveDataReference.Status.PENDING,
            },
        )
        Agreement.objects.update_or_create(
            engagement=engagement,
            kind=Agreement.Kind.CONTRACTOR,
            defaults={
                "status": Agreement.Status.PENDING,
                "external_document_id": "demo-document-reference",
            },
        )
        Credential.objects.update_or_create(
            engagement=engagement,
            center=center,
            kind=Credential.Kind.TRAINING,
            defaults={
                "status": Credential.Status.PENDING,
                "external_reference": "demo-training-reference",
            },
        )
        rate, _ = RateSchedule.objects.update_or_create(
            engagement=engagement,
            center=center,
            unit=RateSchedule.Unit.SESSION,
            starts_on=date(2026, 9, 1),
            defaults={
                "amount": Decimal("75.00"),
                "status": RateSchedule.Status.DRAFT,
                "created_by": admin_user,
            },
        )
        ComplianceTask.objects.update_or_create(
            engagement=engagement,
            kind=ComplianceTask.Kind.W9_VERIFICATION,
            tax_year=2026,
            defaults={
                "status": ComplianceTask.Status.OPEN,
                "due_date": date(2026, 9, 15),
                "external_reference": "demo-task-reference",
            },
        )
        TaxYearSummary.objects.update_or_create(
            engagement=engagement,
            tax_year=2026,
            defaults={
                "total_paid": Decimal("0.00"),
                "filing_threshold": Decimal("600.00"),
                "filing_required": False,
                "status": TaxYearSummary.Status.TRACKING,
            },
        )
        payable, _ = PayableItem.objects.update_or_create(
            engagement=engagement,
            center=center,
            service_date=date(2026, 8, 25),
            description="Demo reading session",
            defaults={
                "source_session": session,
                "units": Decimal("1.00"),
                "rate": rate,
                "gross_amount": Decimal("75.00"),
                "status": PayableItem.Status.DRAFT,
                "created_by": admin_user,
            },
        )
        run, _ = PaymentRun.objects.update_or_create(
            payer=payer,
            period_start=date(2026, 8, 16),
            period_end=date(2026, 8, 31),
            defaults={"status": PaymentRun.Status.DRAFT, "created_by": admin_user},
        )
        Payment.objects.update_or_create(
            payment_run=run,
            payable=payable,
            defaults={
                "engagement": engagement,
                "amount": Decimal("75.00"),
                "status": Payment.Status.QUEUED,
                "external_payment_id": "",
            },
        )
        ProviderEvent.objects.update_or_create(
            provider="demo-provider",
            external_event_id="demo-event-001",
            defaults={
                "event_type": "sample.onboarding.preview",
                "payload_hash": "0" * 64,
                "status": ProviderEvent.Status.RECEIVED,
            },
        )

    def _seed_outcomes(self, center, curriculum):
        end = timezone.localdate().replace(day=1) - timedelta(days=1)
        DeIdentifiedOutcomeSnapshot.objects.get_or_create(
            center=center,
            methodology=curriculum.code,
            grade_band="grade_1_2",
            window_type=DeIdentifiedOutcomeSnapshot.WindowType.MONTH,
            window_start=end.replace(day=1),
            window_end=end,
            metric_scope="demo_outcomes",
            aggregate_version="demo-v1",
            defaults={
                "center_key": build_center_key(center.pk),
                "privacy_floor": 5,
                "metrics": {
                    "cohort_students": 5,
                    "completed_sessions": 8,
                    "weighted_accuracy_rate": 80.0,
                },
                "source_counts": {"sessions": 8},
            },
        )
