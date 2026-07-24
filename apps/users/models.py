from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Max
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


class MobileDevice(TimestampedModel):
    class Environment(models.TextChoices):
        SANDBOX = "sandbox", "Sandbox"
        PRODUCTION = "production", "Production"

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="mobile_devices")
    device_id = models.UUIDField()
    push_token = models.CharField(max_length=255, blank=True)
    environment = models.CharField(
        max_length=16,
        choices=Environment.choices,
        default=Environment.SANDBOX,
    )
    app_version = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-last_seen_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "device_id"], name="unique_user_mobile_device"),
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["push_token", "environment"]),
        ]

    def __str__(self):
        return f"{self.user} iOS device {self.device_id}"


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

    class IEPStatus(models.TextChoices):
        NOT_REPORTED = "not_reported", "Not reported"
        NO_IEP = "no_iep", "No IEP"
        ACTIVE = "active", "Active IEP"
        IN_REVIEW = "in_review", "IEP in review"

    class ApprovalStatus(models.TextChoices):
        NOT_REQUIRED = "not_required", "Not required"
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"
        REVOKED = "revoked", "Revoked"

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
    availability_windows = models.JSONField(
        default=list,
        blank=True,
        help_text="Guardian-provided weekly availability windows for future scheduling.",
    )
    iep_status = models.CharField(
        max_length=20,
        choices=IEPStatus.choices,
        default=IEPStatus.NOT_REPORTED,
        db_index=True,
    )
    idea_parent_consent_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.NOT_REQUIRED,
        db_index=True,
    )
    idea_parent_consented_at = models.DateTimeField(null=True, blank=True)
    iep_team_approval_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.NOT_REQUIRED,
        db_index=True,
    )
    iep_team_approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["school", "grade_level"]),
            models.Index(fields=["student_identifier"]),
            models.Index(fields=["iep_status", "idea_parent_consent_status", "iep_team_approval_status"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return " ".join(part for part in [self.first_name, self.last_name] if part)

    @property
    def idea_services_authorized(self):
        if self.iep_status != self.IEPStatus.ACTIVE:
            return True
        if self.pk:
            consent_record = self.consent_records.filter(
                consent_type=ConsentRecord.ConsentType.IDEA_IEP,
                is_deleted=False,
            ).order_by("-version", "-created_at").first()
            if consent_record is not None:
                return consent_record.is_effective
        return (
            self.idea_parent_consent_status == self.ApprovalStatus.APPROVED
            and self.idea_parent_consented_at is not None
            and self.iep_team_approval_status == self.ApprovalStatus.APPROVED
            and self.iep_team_approved_at is not None
        )


class ConsentRecord(TimestampedModel, SoftDeleteModel):
    """Append-only formal authorization history for center-scoped services."""

    class ConsentType(models.TextChoices):
        GENERAL = "general", "General"
        IDEA_IEP = "idea_iep", "IDEA / IEP"
        DATA_USE = "data_use", "Data Use"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        GRANTED = "granted", "Granted"
        DENIED = "denied", "Denied"
        REVOKED = "revoked", "Revoked"
        PENDING = "pending", "Pending"

    child = models.ForeignKey(ChildProfile, on_delete=models.PROTECT, related_name="consent_records")
    center = models.ForeignKey("schools.School", on_delete=models.PROTECT, related_name="consent_records")
    consent_type = models.CharField(max_length=24, choices=ConsentType.choices, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    version = models.PositiveIntegerField(editable=False)
    granted_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consent_records_granted",
    )
    granted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    evidence_notes = models.TextField(blank=True)
    source_document_ref = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consent_records_created",
    )

    class Meta:
        ordering = ["-created_at", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["child", "consent_type", "version"],
                name="unique_child_consent_type_version",
            ),
        ]
        indexes = [
            models.Index(fields=["center", "consent_type", "status"]),
            models.Index(fields=["child", "consent_type", "-version"]),
            models.Index(fields=["expires_at", "status"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    @property
    def is_effective(self):
        if self.status != self.Status.GRANTED or self.granted_at is None or self.is_deleted:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    def clean(self):
        errors = {}
        if self.child_id and self.child.school_id != self.center_id:
            errors["center"] = "Consent records must use the child's center."
        if self.status == self.Status.GRANTED and self.granted_at is None:
            errors["granted_at"] = "Granted consent requires a grant timestamp."
        if self.expires_at and self.granted_at and self.expires_at <= self.granted_at:
            errors["expires_at"] = "Consent expiration must be after the grant timestamp."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Consent records are append-only; create a new version.")
        if self.status == self.Status.GRANTED and self.granted_at is None:
            self.granted_at = timezone.now()
        if not self.version:
            with transaction.atomic():
                ChildProfile.objects.select_for_update().get(pk=self.child_id)
                latest_version = (
                    type(self).objects.filter(child_id=self.child_id, consent_type=self.consent_type)
                    .aggregate(value=Max("version"))["value"]
                    or 0
                )
                self.version = latest_version + 1
                self.full_clean()
                return super().save(*args, **kwargs)
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.child} {self.get_consent_type_display()} v{self.version}: {self.get_status_display()}"


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
