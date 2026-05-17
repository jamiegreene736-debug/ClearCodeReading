from django.core.management.base import BaseCommand
from django.utils import timezone

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
            is_staff=True,
            is_superuser=False,
        )
        parent = self._upsert_user(
            email=DEMO_PARENT_EMAIL,
            username="demo-parent",
            first_name="Demo",
            last_name="Parent",
            role=CustomUser.Role.GUARDIAN,
            is_staff=True,
            is_superuser=False,
        )

        child, _ = ChildProfile.objects.update_or_create(
            first_name="Avery",
            last_name="Reader",
            student_identifier="DEMO-AVERY-READER",
            defaults={
                "grade_level": ChildProfile.GradeLevel.GRADE_2,
                "learning_profile": {"demo": True, "reading_goal": "Build confident decoding and fluency."},
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

        self.stdout.write(self.style.SUCCESS("Created Clear Code Reading demo credentials:"))
        self.stdout.write(f"  Admin:   {admin.email} / {DEMO_PASSWORD}")
        self.stdout.write(f"  Teacher: {teacher.email} / {DEMO_PASSWORD}")
        self.stdout.write(f"  Parent:  {parent.email} / {DEMO_PASSWORD}")
        self.stdout.write(f"  Demo child id: {child.id} ({child})")

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
