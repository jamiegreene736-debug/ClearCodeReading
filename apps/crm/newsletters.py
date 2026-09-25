from datetime import timedelta
from email.utils import parseaddr
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.crm.models import Lead, NewsletterCampaign, NewsletterDelivery, NewsletterSubscription


UNSUBSCRIBE_SIGNING_SALT = "apps.crm.newsletter.unsubscribe"
DEVELOPMENT_EMAIL_BACKENDS = {
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
}


class NewsletterSendError(Exception):
    """Base error for a campaign that cannot be sent safely."""


class NewsletterSendInProgress(NewsletterSendError):
    pass


class NoActiveNewsletterSubscribers(NewsletterSendError):
    pass


class NewsletterEmailDeliveryNotConfigured(NewsletterSendError):
    pass


def newsletter_from_email() -> str:
    """Visible From for every campaign and test. Independent of system no-reply mail."""
    configured = getattr(settings, "NEWSLETTER_FROM_EMAIL", "").strip()
    return configured or "ClearCode Reading <hello@clearcodereading.com>"


def newsletter_reply_to() -> str:
    _name, address = parseaddr(newsletter_from_email())
    return address or "hello@clearcodereading.com"


def subscription_for_email(email: str) -> NewsletterSubscription | None:
    normalized = (email or "").strip().lower()
    if not normalized:
        return None
    return NewsletterSubscription.objects.filter(email=normalized).first()


def link_subscription_to_contact(subscription: NewsletterSubscription, lead: Lead | None = None) -> None:
    """Attach the subscription to the contact it belongs to, when one is known."""
    if lead is None:
        matches = list(
            Lead.objects.filter(is_deleted=False, contact_email__iexact=subscription.email).order_by("-updated_at")[:2]
        )
        lead = matches[0] if len(matches) == 1 else None
    if lead is None or subscription.lead_id == lead.pk:
        return
    subscription.lead = lead
    subscription.save(update_fields=["lead", "updated_at"])


def subscribe_contact(lead: Lead, *, consented: bool) -> NewsletterSubscription:
    """Add a CRM contact after staff record that the person agreed to the newsletter."""
    if not consented:
        raise ValidationError("Confirm that this person agreed to receive the newsletter.")
    email = (lead.contact_email or "").strip().lower()
    if len(email) > 254:
        raise ValidationError("This contact’s email address is too long.")
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValidationError("Add a valid email address on the contact before subscribing them.") from exc
    now = timezone.now()
    existing_name = NewsletterSubscription.objects.filter(email=email).values_list("name", flat=True).first()
    subscription, _created = NewsletterSubscription.objects.update_or_create(
        email=email,
        defaults={
            "name": (lead.contact_name or existing_name or "")[:255],
            "lead": lead,
            "status": NewsletterSubscription.Status.ACTIVE,
            "consented_at": now,
            "unsubscribed_at": None,
            "source_path": f"/crm/contacts/{lead.pk}/",
        },
    )
    return subscription


def unsubscribe_subscription(subscription: NewsletterSubscription) -> NewsletterSubscription:
    if subscription.status != NewsletterSubscription.Status.UNSUBSCRIBED:
        subscription.status = NewsletterSubscription.Status.UNSUBSCRIBED
        subscription.unsubscribed_at = timezone.now()
        subscription.save(update_fields=["status", "unsubscribed_at", "updated_at"])
    return subscription


def duplicate_campaign(campaign: NewsletterCampaign, *, created_by) -> NewsletterCampaign:
    subject = campaign.subject
    if not subject.lower().startswith("copy of "):
        subject = f"Copy of {subject}"[:255]
    copy = NewsletterCampaign(
        subject=subject,
        preview_text=campaign.preview_text,
        body=campaign.body,
        body_html=campaign.body_html,
        created_by=created_by,
    )
    copy.full_clean()
    copy.save()
    return copy


def newsletter_delivery_configuration_errors() -> tuple[str, ...]:
    if settings.DEBUG:
        return ()

    errors = []
    if settings.EMAIL_BACKEND in DEVELOPMENT_EMAIL_BACKENDS:
        errors.append("Configure a production email backend.")

    public_url = urlparse(settings.PUBLIC_APP_URL)
    if public_url.scheme != "https" or public_url.hostname in {None, "localhost", "127.0.0.1"}:
        errors.append("Set PUBLIC_APP_URL to the public HTTPS site URL.")
    return tuple(errors)


def make_unsubscribe_token(subscription):
    return signing.dumps(
        {"subscription_id": subscription.pk, "email": subscription.email},
        salt=UNSUBSCRIBE_SIGNING_SALT,
        compress=True,
    )


def resolve_unsubscribe_token(token):
    try:
        payload = signing.loads(token, salt=UNSUBSCRIBE_SIGNING_SALT)
    except signing.BadSignature:
        return None
    return NewsletterSubscription.objects.filter(
        pk=payload.get("subscription_id"),
        email=payload.get("email", "").strip().lower(),
    ).first()


def _unsubscribe_url(subscription: NewsletterSubscription) -> str:
    path = reverse("newsletter_unsubscribe", kwargs={"token": make_unsubscribe_token(subscription)})
    return urljoin(f"{settings.PUBLIC_APP_URL.rstrip('/')}/", path.lstrip("/"))


def campaign_text(campaign: NewsletterCampaign) -> str:
    """Plain-text body: the stored text, or the rich body flattened."""
    if campaign.body_html:
        from apps.crm_email.security import plain_text

        return plain_text(campaign.body_html).strip()
    return campaign.body.strip()


def _delivery_message(campaign, delivery, connection):
    unsubscribe_url = _unsubscribe_url(delivery.subscription)
    text_body = (
        f"{campaign_text(campaign)}\n\n"
        "---\n"
        "You are receiving this because you subscribed to ClearCode Reading updates.\n"
        f"Unsubscribe: {unsubscribe_url}"
    )
    html_body = render_to_string(
        "crm/newsletter_email.html",
        {
            "campaign": campaign,
            "unsubscribe_url": unsubscribe_url,
        },
    )
    message = EmailMultiAlternatives(
        subject=campaign.subject,
        body=text_body,
        from_email=newsletter_from_email(),
        to=[delivery.recipient_email],
        reply_to=[newsletter_reply_to()],
        connection=connection,
        headers={
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
    message.attach_alternative(html_body, "text/html")
    return message


def send_newsletter_test(campaign: NewsletterCampaign, recipient_email: str) -> int:
    """Send one copy of the campaign to a team member; no delivery is recorded."""
    text_body = (
        f"[TEST] {campaign_text(campaign)}\n\n"
        "---\nThis is a test copy sent from the CRM newsletter. "
        "Subscribers see an unsubscribe link here."
    )
    html_body = render_to_string(
        "crm/newsletter_email.html",
        {"campaign": campaign, "unsubscribe_url": "#", "test_copy": True},
    )
    message = EmailMultiAlternatives(
        subject=f"[TEST] {campaign.subject}",
        body=text_body,
        from_email=newsletter_from_email(),
        to=[recipient_email],
        reply_to=[newsletter_reply_to()],
    )
    message.attach_alternative(html_body, "text/html")
    return message.send(fail_silently=False)


def _claim_campaign(campaign_id, sent_by):
    with transaction.atomic():
        campaign = NewsletterCampaign.objects.select_for_update().get(pk=campaign_id)
        if campaign.status == NewsletterCampaign.Status.SENT:
            return campaign, False

        stale_after = timedelta(minutes=getattr(settings, "NEWSLETTER_SEND_STALE_MINUTES", 30))
        sending_is_fresh = (
            campaign.status == NewsletterCampaign.Status.SENDING
            and campaign.sending_started_at
            and campaign.sending_started_at > timezone.now() - stale_after
        )
        if sending_is_fresh:
            raise NewsletterSendInProgress("This newsletter is already being sent.")

        if not campaign.deliveries.exists():
            subscriptions = NewsletterSubscription.objects.filter(
                status=NewsletterSubscription.Status.ACTIVE,
            ).only("id", "email")
            NewsletterDelivery.objects.bulk_create(
                [
                    NewsletterDelivery(
                        campaign=campaign,
                        subscription=subscription,
                        recipient_email=subscription.email,
                    )
                    for subscription in subscriptions.iterator()
                ]
            )

        if not campaign.deliveries.filter(status__in=[NewsletterDelivery.Status.PENDING, NewsletterDelivery.Status.FAILED]).exists():
            if campaign.deliveries.exists():
                _finalize_campaign(campaign.pk)
                campaign.refresh_from_db()
                return campaign, False
            raise NoActiveNewsletterSubscribers("There are no active newsletter subscribers.")

        campaign.status = NewsletterCampaign.Status.SENDING
        campaign.sent_by = sent_by
        campaign.sending_started_at = timezone.now()
        campaign.save(update_fields=["status", "sent_by", "sending_started_at", "updated_at"])
        return campaign, True


def _record_delivery_failure(delivery, error):
    delivery.status = NewsletterDelivery.Status.FAILED
    delivery.attempts += 1
    delivery.last_error = f"{type(error).__name__}: {error}"[:1000]
    delivery.save(update_fields=["status", "attempts", "last_error", "updated_at"])


def _finalize_campaign(campaign_id):
    totals = NewsletterDelivery.objects.filter(campaign_id=campaign_id).aggregate(
        recipient_count=Count("id"),
        delivered_count=Count("id", filter=Q(status=NewsletterDelivery.Status.SENT)),
        failed_count=Count("id", filter=Q(status=NewsletterDelivery.Status.FAILED)),
    )
    failed_count = totals["failed_count"] or 0
    NewsletterCampaign.objects.filter(pk=campaign_id).update(
        status=(
            NewsletterCampaign.Status.PARTIALLY_FAILED
            if failed_count
            else NewsletterCampaign.Status.SENT
        ),
        recipient_count=totals["recipient_count"] or 0,
        delivered_count=totals["delivered_count"] or 0,
        failed_count=failed_count,
        sent_at=timezone.now(),
        updated_at=timezone.now(),
    )


def send_newsletter_campaign(campaign_id: int, *, sent_by: object = None) -> NewsletterCampaign:
    configuration_errors = newsletter_delivery_configuration_errors()
    if configuration_errors:
        raise NewsletterEmailDeliveryNotConfigured(" ".join(configuration_errors))

    campaign, should_send = _claim_campaign(campaign_id, sent_by)
    if not should_send:
        return campaign

    deliveries = list(
        NewsletterDelivery.objects.filter(
            campaign=campaign,
            status__in=[NewsletterDelivery.Status.PENDING, NewsletterDelivery.Status.FAILED],
        ).select_related("subscription")
    )
    connection = get_connection(fail_silently=False)
    try:
        connection.open()
        for delivery in deliveries:
            if not NewsletterSubscription.objects.filter(
                pk=delivery.subscription_id,
                status=NewsletterSubscription.Status.ACTIVE,
                email=delivery.recipient_email,
            ).exists():
                delivery.status = NewsletterDelivery.Status.SKIPPED
                delivery.last_error = "Subscriber opted out before delivery."
                delivery.save(update_fields=["status", "last_error", "updated_at"])
                continue

            try:
                sent_count = _delivery_message(campaign, delivery, connection).send(fail_silently=False)
                if sent_count != 1:
                    raise NewsletterSendError("The email backend did not confirm delivery.")
            # Email backends raise provider-specific exception classes. Record the
            # individual failure so one bad recipient cannot hide later results.
            except Exception as error:
                _record_delivery_failure(delivery, error)
                continue

            sent_at = timezone.now()
            delivery.status = NewsletterDelivery.Status.SENT
            delivery.attempts += 1
            delivery.sent_at = sent_at
            delivery.last_error = ""
            delivery.save(update_fields=["status", "attempts", "sent_at", "last_error", "updated_at"])
            NewsletterSubscription.objects.filter(pk=delivery.subscription_id).update(
                last_sent_at=sent_at,
                updated_at=sent_at,
            )
    # Connection/setup failures can also be backend-specific; every unsent
    # delivery remains explicit and retryable.
    except Exception as error:
        for delivery in deliveries:
            if delivery.status in {NewsletterDelivery.Status.PENDING, NewsletterDelivery.Status.FAILED}:
                _record_delivery_failure(delivery, error)
    finally:
        connection.close()
        _finalize_campaign(campaign.pk)

    campaign.refresh_from_db()
    return campaign
