from django.db import models

from apps.core.models import TimeStampedModel
from apps.organizations.models import Organization


class Subscription(TimeStampedModel):
    class Status(models.TextChoices):
        TRIALING = "trialing", "Trialing"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        CANCELED = "canceled", "Canceled"

    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="subscription")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.TRIALING)
    plan = models.CharField(max_length=80, default="starter")
    external_customer_id = models.CharField(max_length=120, blank=True)
    external_subscription_id = models.CharField(max_length=120, blank=True)

    def __str__(self):
        return f"{self.organization} - {self.plan}"
