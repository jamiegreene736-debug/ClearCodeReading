from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Organization(TimeStampedModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=80, unique=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="Membership", related_name="organizations")

    def __str__(self):
        return self.name


class Membership(TimeStampedModel):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)

    class Meta:
        unique_together = ("organization", "user")

    def __str__(self):
        return f"{self.user} in {self.organization}"
