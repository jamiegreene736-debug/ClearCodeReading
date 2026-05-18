from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models.signals import post_save
from django.utils import timezone

from apps.assessments.models import Assessment, AssessmentResult
from apps.curriculum.models import ChildLessonAssignment, LessonTemplate, TeacherLessonTemplate
from apps.notifications.signals import handle_assessment_status_change
from apps.users.models import ChildProfile, ConsentLog, CustomUser, GuardianRelationship


DEMO_PASSWORD = "ClearCodeDemo!2026"
DEMO_ADMIN_EMAIL = "admin@clearcodereading.com"
DEMO_PARENT_EMAIL = "parent@clearcodereading.com"
DEMO_TEACHER_EMAIL = "teacher@clearcodereading.com"


class Command(BaseCommand):
    help = "Create demo Clear Code Reading login credentials, a demo child, and COPPA consent records."

    def handle(self, *args, **options):
        admin = self._upsert_user(
            email=DEMO_ADMIN_EMAIL,
            username="demo-admin",
            first_name="Demo",
            last_name="Admin",
            role=CustomUser.Role.SUPER_ADMIN,
            is_staff=True,
            is_superuser=True,
        )
        teacher = self._upsert_user(
            email=DEMO_TEACHER_EMAIL,
            username="demo-teacher",
            first_name="Demo",
            last_name="Teacher",
            role=CustomUser.Role.TEACHER,
            is_staff=False,
            is_superuser=False,
        )
        parent = self._upsert_user(
            email=DEMO_PARENT_EMAIL,
            username="demo-parent",
            first_name="Demo",
            last_name="Parent",
            role=CustomUser.Role.GUARDIAN,
            is_staff=False,
            is_superuser=False,
        )

        child, _ = ChildProfile.objects.update_or_create(
            first_name="Avery",
            last_name="Reader",
            student_identifier="DEMO-AVERY-READER",
            defaults={
                "grade_level": ChildProfile.GradeLevel.GRADE_2,
                "learning_profile": {
                    "demo": True,
                    "reading_goal": "Build confident decoding and fluency.",
                    "assigned_teacher_id": teacher.id,
                    "assigned_teacher_name": teacher.get_full_name() or teacher.email,
                    "assigned_teacher_email": teacher.email,
                },
                "accommodations": [],
                "is_deleted": False,
                "deleted_at": None,
            },
        )
        relationship, _ = GuardianRelationship.objects.update_or_create(
            guardian=parent,
            child=child,
            defaults={
                "relationship_type": GuardianRelationship.RelationshipType.PARENT,
                "is_primary": True,
                "consent_status": GuardianRelationship.ConsentStatus.GRANTED,
                "consent_expires_at": None,
                "permissions": {"assessment": True, "progress": True, "school_sharing": True},
                "is_deleted": False,
                "deleted_at": None,
            },
        )

        for consent_type in [
            ConsentLog.ConsentType.TERMS,
            ConsentLog.ConsentType.PRIVACY,
            ConsentLog.ConsentType.DATA_PROCESSING,
            ConsentLog.ConsentType.SCHOOL_SHARING,
            ConsentLog.ConsentType.ASSESSMENT,
        ]:
            ConsentLog.objects.update_or_create(
                guardian_relationship=relationship,
                guardian=parent,
                child=child,
                consent_type=consent_type,
                defaults={
                    "status": ConsentLog.Status.GRANTED,
                    "version": "demo-2026-05",
                    "source": "seed_demo_login",
                    "expires_at": None,
                    "metadata": {"demo": True, "seeded_at": timezone.now().isoformat()},
                },
            )

        completed_assessment, review_assessment = self._seed_demo_assessments(child=child, teacher=teacher)
        template_count, lesson_count = self._seed_demo_lessons(child=child, teacher=teacher, admin=admin)

        self.stdout.write(self.style.SUCCESS("Created Clear Code Reading demo credentials:"))
        self.stdout.write(f"  Admin:   {admin.email} / {DEMO_PASSWORD}")
        self.stdout.write(f"  Teacher: {teacher.email} / {DEMO_PASSWORD}")
        self.stdout.write(f"  Parent:  {parent.email} / {DEMO_PASSWORD}")
        self.stdout.write(f"  Demo child id: {child.id} ({child})")
        self.stdout.write(f"  Demo completed assessment id: {completed_assessment.id}")
        self.stdout.write(f"  Demo review assessment id: {review_assessment.id}")
        self.stdout.write(f"  Demo lesson templates assigned to teacher: {template_count}")
        self.stdout.write(f"  Demo child lesson assignments: {lesson_count}")

    def _seed_demo_lessons(self, child, teacher, admin):
        templates = list(
            LessonTemplate.objects.filter(
                slug__in=["beginning-sounds-sprint", "short-vowel-decoding", "fluency-builder"],
                is_active=True,
                is_deleted=False,
            )
        )
        for template in templates:
            TeacherLessonTemplate.objects.update_or_create(
                teacher=teacher,
                template=template,
                defaults={
                    "assigned_by": admin,
                    "notes": "Demo access for teacher lesson assignment workflow.",
                    "is_deleted": False,
                    "deleted_at": None,
                },
            )

        lesson_count = 0
        for template in templates[:2]:
            ChildLessonAssignment.objects.update_or_create(
                child=child,
                template=template,
                status=ChildLessonAssignment.Status.ASSIGNED,
                defaults={
                    "assigned_by": teacher,
                    "due_date": timezone.localdate() + timedelta(days=7),
                    "teacher_notes": "Demo plan: complete this before the next reading check-in.",
                    "is_deleted": False,
                    "deleted_at": None,
                },
            )
            lesson_count += 1
        return len(templates), lesson_count

    def _seed_demo_assessments(self, child, teacher):
        signal_disconnected = post_save.disconnect(
            receiver=handle_assessment_status_change,
            sender=Assessment,
        )
        try:
            return self._upsert_demo_assessments(child=child, teacher=teacher)
        finally:
            if signal_disconnected:
                post_save.connect(
                    receiver=handle_assessment_status_change,
                    sender=Assessment,
                )

    def _upsert_demo_assessments(self, child, teacher):
        completed_assessment, _ = Assessment.objects.update_or_create(
            child=child,
            title="Demo Reading Survey - Avery Reader",
            defaults={
                "assigned_by": teacher,
                "assessment_type": Assessment.AssessmentType.DIAGNOSTIC,
                "status": Assessment.Status.COMPLETED,
                "started_at": timezone.now(),
                "survey_completed_at": timezone.now(),
                "completed_at": timezone.now(),
                "overall_score": 76,
                "reading_age": "8.2",
                "raw_score": "7.60",
                "max_score": "10.00",
                "scoring": {
                    "reading_survey": {
                        "overall_score": 76,
                        "reading_age": 8.2,
                        "final_message": "You are reading at an 8.2-year-old level",
                    }
                },
                "recommendations": ["Fluency", "Advanced decoding"],
                "metadata": {"demo": True, "source": "seed_demo_login"},
                "is_deleted": False,
                "deleted_at": None,
            },
        )
        AssessmentResult.objects.update_or_create(
            assessment=completed_assessment,
            defaults={
                "reading_age": "8.2",
                "grade_equivalent": "Grade 3",
                "final_scores": {
                    "overall_score": 76,
                    "response_count": 10,
                    "final_message": "You are reading at an 8.2-year-old level",
                },
                "category_breakdown": {
                    "phonemic_awareness": {"label": "Phonemic awareness", "score": 100, "responses": 1},
                    "letter_sound": {"label": "Letter sounds", "score": 100, "responses": 1},
                    "phonics": {"label": "Phonics / decoding", "score": 82, "responses": 2},
                    "sight_words": {"label": "Sight words", "score": 70, "responses": 1},
                    "fluency": {"label": "Fluency", "score": 58, "responses": 1},
                    "vocabulary": {"label": "Vocabulary", "score": 75, "responses": 1},
                    "comprehension": {"label": "Comprehension", "score": 72, "responses": 1},
                    "writing_readiness": {"label": "Writing readiness", "score": 80, "responses": 1},
                    "confidence": {"label": "Reading confidence", "score": 65, "responses": 1},
                },
                "strengths": ["Phonemic awareness", "Letter sounds", "Phonics / decoding"],
                "growth_areas": ["Fluency", "Reading confidence"],
                "teacher_summary": (
                    "Avery shows strong sound awareness and letter-sound knowledge. "
                    "Next support should focus on smoother oral reading, confidence, and longer word decoding."
                ),
                "evaluator_notes": "Demo evaluator note: Avery benefits from warm prompts and short repeated-reading practice.",
                "metadata": {"demo": True, "source": "seed_demo_login"},
                "is_deleted": False,
                "deleted_at": None,
            },
        )

        review_assessment, _ = Assessment.objects.update_or_create(
            child=child,
            title="Demo Human Review Queue Item",
            defaults={
                "assigned_by": teacher,
                "assessment_type": Assessment.AssessmentType.SCREENING,
                "status": Assessment.Status.HUMAN_REVIEW,
                "started_at": timezone.now(),
                "survey_completed_at": timezone.now(),
                "overall_score": 64,
                "reading_age": "7.1",
                "raw_score": "6.40",
                "max_score": "10.00",
                "scoring": {
                    "reading_survey": {
                        "overall_score": 64,
                        "reading_age": 7.1,
                        "final_message": "You are reading at a 7.1-year-old level",
                    }
                },
                "recommendations": ["Comprehension", "Fluency"],
                "metadata": {"demo": True, "source": "seed_demo_login", "needs_human_notes": True},
                "is_deleted": False,
                "deleted_at": None,
            },
        )
        AssessmentResult.objects.update_or_create(
            assessment=review_assessment,
            defaults={
                "reading_age": "7.1",
                "grade_equivalent": "Grade 2",
                "final_scores": {
                    "overall_score": 64,
                    "response_count": 10,
                    "final_message": "You are reading at a 7.1-year-old level",
                },
                "category_breakdown": {
                    "phonemic_awareness": {"label": "Phonemic awareness", "score": 86, "responses": 1},
                    "letter_sound": {"label": "Letter sounds", "score": 78, "responses": 1},
                    "phonics": {"label": "Phonics / decoding", "score": 68, "responses": 2},
                    "fluency": {"label": "Fluency", "score": 45, "responses": 1},
                    "comprehension": {"label": "Comprehension", "score": 50, "responses": 1},
                    "confidence": {"label": "Reading confidence", "score": 55, "responses": 1},
                },
                "strengths": ["Phonemic awareness", "Letter sounds"],
                "growth_areas": ["Fluency", "Comprehension"],
                "teacher_summary": "Demo queue item: confirm oral reading fluency and comprehension before final placement.",
                "evaluator_notes": "",
                "metadata": {"demo": True, "source": "seed_demo_login"},
                "is_deleted": False,
                "deleted_at": None,
            },
        )
        return completed_assessment, review_assessment

    def _upsert_user(self, email, username, first_name, last_name, role, is_staff, is_superuser):
        user, _ = CustomUser.objects.update_or_create(
            email=email,
            defaults={
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "role": role,
                "is_staff": is_staff,
                "is_superuser": is_superuser,
                "is_active": True,
                "is_deleted": False,
                "deleted_at": None,
                "metadata": {"demo": True},
            },
        )
        user.set_password(DEMO_PASSWORD)
        user.save(update_fields=["password", "updated_at"])
        return user
