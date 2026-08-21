from datetime import timedelta
from urllib.parse import urljoin

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.crm.models import NewsletterCampaign, NewsletterDelivery, NewsletterSubscription


UNSUBSCRIBE_SIGNING_SALT = "apps.crm.newsletter.unsubscribe"


class NewsletterSendError(Exception):
    """Base error for a campaign that cannot be sent safely."""


class NewsletterSendInProgress(NewsletterSendError):
    pass


class NoActiveNewsletterSubscribers(NewsletterSendError):
    pass


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


def _unsubscribe_url(subscription):
    path = reverse("newsletter_unsubscribe", kwargs={"token": make_unsubscribe_token(subscription)})
    return urljoin(f"{settings.PUBLIC_APP_URL.rstrip('/')}/", path.lstrip("/"))


def _delivery_message(campaign, delivery, connection):
    unsubscribe_url = _unsubscribe_url(delivery.subscription)
    text_body = (
        f"{campaign.body.strip()}\n\n"
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
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[delivery.recipient_email],
        connection=connection,
        headers={
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
    message.attach_alternative(html_body, "text/html")
    return message


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


def send_newsletter_campaign(campaign_id, *, sent_by=None):
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
