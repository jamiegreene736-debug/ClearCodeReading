from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])


class CustomUser(AbstractUser, TimestampedModel, SoftDeleteModel):
    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super Admin"
        SCHOOL_ADMIN = "school_admin", "School Admin"
        TEACHER = "teacher", "Teacher"
        GUARDIAN = "guardian", "Guardian"
        STUDENT = "student", "Student"

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.GUARDIAN, db_index=True)
    phone_number = models.CharField(max_length=32, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        ordering = ["last_name", "first_name", "email"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["role", "is_active"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return self.get_full_name() or self.email


class Profile(TimestampedModel, SoftDeleteModel):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=255, blank=True)
    avatar = models.ImageField(upload_to="profiles/avatars/", blank=True)
    timezone = models.CharField(max_length=64, default="UTC")
    preferences = models.JSONField(default=dict, blank=True)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["user__email"]
        indexes = [
            models.Index(fields=["user", "is_deleted"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.display_name or str(self.user)


class ChildProfile(TimestampedModel, SoftDeleteModel):
    class GradeLevel(models.TextChoices):
        PRE_K = "pre_k", "Pre-K"
        KINDERGARTEN = "kindergarten", "Kindergarten"
        GRADE_1 = "grade_1", "Grade 1"
        GRADE_2 = "grade_2", "Grade 2"
        GRADE_3 = "grade_3", "Grade 3"
        GRADE_4 = "grade_4", "Grade 4"
        GRADE_5 = "grade_5", "Grade 5"
        OTHER = "other", "Other"

    user = models.OneToOneField(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_profile",
        limit_choices_to={"role": CustomUser.Role.STUDENT},
    )
    first_name = models.CharField(max_length=120)
    last_name = models.CharField(max_length=120, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    grade_level = models.CharField(max_length=32, choices=GradeLevel.choices, blank=True)
    school = models.ForeignKey("schools.School", on_delete=models.SET_NULL, null=True, blank=True, related_name="children")
    student_identifier = models.CharField(max_length=120, blank=True)
    learning_profile = models.JSONField(default=dict, blank=True)
    accommodations = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["school", "grade_level"]),
            models.Index(fields=["student_identifier"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return " ".join(part for part in [self.first_name, self.last_name] if part)


class GuardianRelationship(TimestampedModel, SoftDeleteModel):
    class RelationshipType(models.TextChoices):
        PARENT = "parent", "Parent"
        LEGAL_GUARDIAN = "legal_guardian", "Legal Guardian"
        CAREGIVER = "caregiver", "Caregiver"
        OTHER = "other", "Other"

    class ConsentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        GRANTED = "granted", "Granted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    guardian = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="guardian_relationships",
        limit_choices_to={"role": CustomUser.Role.GUARDIAN},
    )
    child = models.ForeignKey(ChildProfile, on_delete=models.CASCADE, related_name="guardian_relationships")
    relationship_type = models.CharField(max_length=32, choices=RelationshipType.choices)
    is_primary = models.BooleanField(default=False)
    consent_status = models.CharField(max_length=20, choices=ConsentStatus.choices, default=ConsentStatus.PENDING)
    consent_expires_at = models.DateTimeField(null=True, blank=True)
    permissions = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["child__last_name", "child__first_name", "-is_primary"]
        constraints = [
            models.UniqueConstraint(fields=["guardian", "child"], name="unique_guardian_child_relationship"),
        ]
        indexes = [
            models.Index(fields=["guardian", "child"]),
            models.Index(fields=["child", "is_primary"]),
            models.Index(fields=["consent_status", "consent_expires_at"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.guardian} -> {self.child}"


class ConsentLog(TimestampedModel):
    class ConsentType(models.TextChoices):
        TERMS = "terms", "Terms"
        PRIVACY = "privacy", "Privacy"
        DATA_PROCESSING = "data_processing", "Data Processing"
        SCHOOL_SHARING = "school_sharing", "School Sharing"
        ASSESSMENT = "assessment", "Assessment"

    class Status(models.TextChoices):
        GRANTED = "granted", "Granted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    guardian_relationship = models.ForeignKey(
        GuardianRelationship,
        on_delete=models.CASCADE,
        related_name="consent_logs",
        null=True,
        blank=True,
    )
    guardian = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="consent_logs")
    child = models.ForeignKey(ChildProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="consent_logs")
    consent_type = models.CharField(max_length=32, choices=ConsentType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, db_index=True)
    version = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=80, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["guardian", "consent_type", "status"]),
            models.Index(fields=["child", "consent_type", "status"]),
            models.Index(fields=["guardian_relationship", "created_at"]),
        ]

    def __str__(self):
        return f"{self.consent_type} {self.status} at {self.created_at:%Y-%m-%d}"


class AuditLog(TimestampedModel):
    actor = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    action = models.CharField(max_length=120, db_index=True)
    entity_type = models.CharField(max_length=120, db_index=True)
    entity_id = models.CharField(max_length=120, blank=True, db_index=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action} {self.entity_type}:{self.entity_id}"


@receiver(post_save, sender=CustomUser)
def create_user_profile(sender, instance, created, **kwargs):
    if created and not hasattr(instance, "profile"):
        Profile.objects.create(user=instance)


@receiver(post_save, sender=ConsentLog)
def update_relationship_consent_status(sender, instance, **kwargs):
    relationship = instance.guardian_relationship
    if relationship is None:
        return

    status_map = {
        ConsentLog.Status.GRANTED: GuardianRelationship.ConsentStatus.GRANTED,
        ConsentLog.Status.REVOKED: GuardianRelationship.ConsentStatus.REVOKED,
        ConsentLog.Status.EXPIRED: GuardianRelationship.ConsentStatus.EXPIRED,
    }
    relationship.consent_status = status_map[instance.status]
    relationship.consent_expires_at = instance.expires_at
    relationship.save(update_fields=["consent_status", "consent_expires_at", "updated_at"])
