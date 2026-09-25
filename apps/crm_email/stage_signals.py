"""Capture entry events without doing provider I/O in the deal transaction."""

from typing import Any

from django.db import connection
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.crm.models import Lead, Opportunity
from apps.crm_email.models import StageEmailDelivery, StageEmailPilot
from apps.crm_email.stage_emails import TEST_RECIPIENT

SURVEY_FAMILY_KEY = "survey_family_enrollment"
SURVEY_GENERAL_KEY = "survey_general"
SURVEY_KEYS = (SURVEY_FAMILY_KEY, SURVEY_GENERAL_KEY)


def active_pilot() -> StageEmailPilot | None:
    return StageEmailPilot.objects.filter(enabled=True).order_by("pk").first()


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
    pilot = active_pilot()
    if pilot:
        StageEmailDelivery.objects.get_or_create(
            deal=deal,
            defaults={
                "lead": deal.lead,
                "pilot": pilot,
                "pipeline": deal.pipeline,
                "template_key": getattr(instance, "_email_template_key", ""),
            },
        )


def route_survey_deliveries(
    lead: Lead, *, family: bool, deals: list[Opportunity]
) -> None:
    """Queue the one survey introduction email and drop the deal emails.

    The survey's "which best describes your situation" answer decides the
    wording: every parent answer gets the Families & Enrollment introduction
    and the community answer gets the general introduction. The engagement
    checkboxes only route deals, so any first-stage emails those deals
    captured are cancelled here before anything is queued. The introduction
    belongs to the contact, not a deal, so it still goes out when the answers
    create no deal and when a family joins the waitlist straight away.
    """
    key = SURVEY_FAMILY_KEY if family else SURVEY_GENERAL_KEY
    for deal in deals:
        StageEmailDelivery.objects.filter(
            deal=deal, message__isnull=True, cancelled=False
        ).update(cancelled=True, error="Covered by the survey introduction email.")
    if lead.is_deleted or lead.contact_email.strip().lower() != TEST_RECIPIENT:
        return
    pilot = active_pilot()
    if pilot is None:
        return
    if StageEmailDelivery.objects.filter(
        lead=lead, deal__isnull=True, template_key__in=SURVEY_KEYS, cancelled=False
    ).exists():
        return
    StageEmailDelivery.objects.create(
        lead=lead, pilot=pilot, pipeline="", template_key=key
    )
