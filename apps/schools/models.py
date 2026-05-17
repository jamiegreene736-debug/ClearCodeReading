from django.conf import settings
from django.db import models
from django.utils import timezone
from django_tenants.models import TenantMixin


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


class School(TenantMixin, TimestampedModel, SoftDeleteModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=120, unique=True)
    district = models.CharField(max_length=255, blank=True)
    address = models.JSONField(default=dict, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    branding = models.JSONField(default=dict, blank=True)
    paid_until = models.DateField(null=True, blank=True)
    on_trial = models.BooleanField(default=True)
    auto_create_schema = True
    auto_drop_schema = False

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["schema_name"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return self.name


class SchoolMembership(TimestampedModel, SoftDeleteModel):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        TEACHER = "teacher", "Teacher"
        SPECIALIST = "specialist", "Specialist"
        VIEWER = "viewer", "Viewer"

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="school_memberships")
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.TEACHER, db_index=True)
    title = models.CharField(max_length=120, blank=True)
    permissions = models.JSONField(default=dict, blank=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="school_membership_invites",
    )
    joined_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["school__name", "user__last_name", "user__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["school", "user"], name="unique_school_membership"),
        ]
        indexes = [
            models.Index(fields=["school", "role"]),
            models.Index(fields=["user", "role"]),
            models.Index(fields=["is_deleted", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user} at {self.school}"
