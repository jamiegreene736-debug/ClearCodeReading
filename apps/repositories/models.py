from django.db import models

from apps.core.models import TimeStampedModel
from apps.organizations.models import Organization


class Repository(TimeStampedModel):
    class Provider(models.TextChoices):
        GITHUB = "github", "GitHub"
        GITLAB = "gitlab", "GitLab"
        BITBUCKET = "bitbucket", "Bitbucket"
        OTHER = "other", "Other"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="repositories")
    provider = models.CharField(max_length=30, choices=Provider.choices, default=Provider.GITHUB)
    name = models.CharField(max_length=255)
    full_name = models.CharField(max_length=255)
    clone_url = models.URLField()
    default_branch = models.CharField(max_length=120, default="main")
    external_id = models.CharField(max_length=120, blank=True)

    class Meta:
        unique_together = ("organization", "provider", "full_name")

    def __str__(self):
        return self.full_name


class CodeFile(TimeStampedModel):
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name="files")
    path = models.CharField(max_length=1024)
    language = models.CharField(max_length=80, blank=True)
    checksum = models.CharField(max_length=128, blank=True)

    class Meta:
        unique_together = ("repository", "path")

    def __str__(self):
        return self.path
