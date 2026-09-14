"""Presentation of persisted assessment invitation delivery receipts."""

from dataclasses import dataclass

from django.db.models import Q

from apps.crm.inventory_models import InventoryInvitation, InventoryMail


@dataclass(frozen=True)
class DeliveryFeedback:
    title: str
    description: str
    tone: str
    refresh: bool = False
    mail: InventoryMail | None = None


def delivery_feedback(invitation: InventoryInvitation) -> DeliveryFeedback:
    mail = (
        invitation.emails.filter(
            Q(key__startswith="inventory_send_") | Q(key__startswith="reminder-")
        )
        .select_related("provider_message")
        .order_by("-created_at", "-pk")
        .first()
    )
    if mail is None:
        return DeliveryFeedback(
            "No invitation email recorded",
            "There is no recorded invitation send for this assessment.",
            "neutral",
        )
    label = "Reminder" if mail.key.startswith("reminder-") else "Assessment email"
    if mail.status == InventoryMail.Status.SENT:
        return DeliveryFeedback(
            f"{label} sent",
            "The email provider confirmed sending. This does not confirm that the recipient has opened it.",
            "success",
            mail=mail,
        )
    if mail.status == InventoryMail.Status.FAILED:
        return DeliveryFeedback(
            f"{label} not sent",
            mail.error
            or "Sending failed. Check email settings, then retry the saved email below.",
            "error",
            mail=mail,
        )
    if mail.status == InventoryMail.Status.SENDING:
        return DeliveryFeedback(
            f"{label}: sending confirmation pending",
            "We have not yet received confirmation from the email provider. Do not send another copy while this is being checked.",
            "waiting",
            refresh=True,
            mail=mail,
        )
    return DeliveryFeedback(
        f"{label} queued",
        "Your invitation is saved and waiting to send. This page updates when the email provider confirms the result.",
        "waiting",
        refresh=True,
        mail=mail,
    )
