"""Capture entry events without doing provider I/O in the deal transaction."""

from typing import Any

from django.db import connection
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.crm.models import Opportunity
from apps.crm_email.models import StageEmailDelivery, StageEmailPilot
from apps.crm_email.stage_emails import TEST_RECIPIENT


@receiver(pre_save, sender=Opportunity)
def remember_stage(
    sender: type[Opportunity], instance: Opportunity, **kwargs: Any
) -> None:
    if kwargs.get("raw") or getattr(connection, "schema_name", "") != "public":
        return
    instance._previous_email_stage = (  # type: ignore[attr-defined]
        Opportunity.objects.filter(pk=instance.pk)
        .values_list("pipeline", "stage")
        .first()
        if instance.pk
        else None
    )


@receiver(post_save, sender=Opportunity)
def capture_entry(
    sender: type[Opportunity], instance: Opportunity, **kwargs: Any
) -> None:
    if kwargs.get("raw") or getattr(connection, "schema_name", "") != "public":
        return
    previous = getattr(instance, "_previous_email_stage", None)
    if not kwargs.get("created") and previous == (instance.pipeline, instance.stage):
        return
    # Re-read because update_fields may have excluded unsaved stage/lead changes.
    deal = Opportunity.objects.select_related("lead").get(pk=instance.pk)
    if (
        deal.is_deleted
        or not deal.lead
        or deal.lead.is_deleted
        or deal.pipeline not in Opportunity.Pipeline.values
        or deal.stage != Opportunity.initial_stage_for_pipeline(deal.pipeline)
        or (not kwargs.get("created") and previous == (deal.pipeline, deal.stage))
    ):
        return
    if deal.lead.contact_email.strip().lower() != TEST_RECIPIENT:
        return
    pilot = (
        StageEmailPilot.objects.filter(
            enabled=True,
        )
        .order_by("pk")
        .first()
    )
    if pilot:
        StageEmailDelivery.objects.get_or_create(
            deal=deal,
            defaults={
                "pilot": pilot,
                "pipeline": deal.pipeline,
                "template_key": getattr(instance, "_email_template_key", ""),
            },
        )


def route_survey_deliveries(deals: list[Opportunity]) -> None:
    """Point survey-created first-stage emails at the survey wording.

    Families get the survey Families & Enrollment email. Every other pipeline
    shares one general survey email, so only the first such deal keeps its
    delivery and the rest are cancelled before anything is queued.
    """
    general_sent = False
    for deal in deals:
        delivery = StageEmailDelivery.objects.filter(
            deal=deal, message__isnull=True, cancelled=False
        ).first()
        if delivery is None:
            continue
        if deal.pipeline == Opportunity.Pipeline.FAMILY_ENROLLMENT:
            delivery.template_key = "survey_family_enrollment"
            delivery.save(update_fields=["template_key"])
        elif general_sent:
            delivery.cancelled = True
            delivery.error = "Covered by the single survey email for other pipelines."
            delivery.save(update_fields=["cancelled", "error"])
        else:
            delivery.template_key = "survey_general"
            delivery.save(update_fields=["template_key"])
            general_sent = True
