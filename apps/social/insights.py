"""Bounded, leased analytics collection, isolated from publishing."""

from __future__ import annotations

import logging
import random
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.social.exceptions import SocialError
from apps.social.insights_api import InsightsError, fetch_metrics
from apps.social.models import (
    InsightSnapshot,
    PublicationInsights,
    SocialAccount,
    SocialPublication,
)

logger = logging.getLogger(__name__)


def sync_publication(publication_id: int) -> bool:
    now = timezone.now()
    with transaction.atomic():
        publication = SocialPublication.objects.select_for_update().get(
            pk=publication_id
        )
        if publication.status != "published" or not publication.external_id:
            return False
        state, _ = PublicationInsights.objects.get_or_create(publication=publication)
        if (state.lease_until and state.lease_until > now) or (
            state.next_sync_at and state.next_sync_at > now
        ):
            return False
        account = SocialAccount.objects.filter(network=publication.network).first()
        if account is None or not account.is_connected:
            state.last_error = (
                "Not connected. Connect this platform to collect performance."
            )
            state.checked_at, state.next_sync_at = now, now + timedelta(days=1)
            state.save()
            return False
        if account.insights_retry_after and account.insights_retry_after > now:
            return False
        state.lease_until = now + timedelta(minutes=5)
        state.save(update_fields=["lease_until", "updated_at"])
    try:
        result = fetch_metrics(publication, account)
    except (InsightsError, SocialError) as exc:
        kind = exc.kind if isinstance(exc, InsightsError) else "permission"
        message = (
            str(exc)
            if isinstance(exc, InsightsError)
            else "Reconnect with analytics access in Connections."
        )
        with transaction.atomic():
            state = PublicationInsights.objects.select_for_update().get(pk=state.pk)
            state.failures += 1
            seconds = (
                86400
                if kind == "permission"
                else min(86400, 300 * 2 ** min(state.failures, 8))
            )
            state.next_sync_at = now + timedelta(
                seconds=seconds + random.randint(0, 60)
            )
            state.checked_at, state.last_error, state.lease_until = now, message, None
            state.save()
            if kind in {"permission", "rate_limit", "temporary"}:
                SocialAccount.objects.filter(pk=account.pk).update(
                    insights_retry_after=state.next_sync_at, insights_error=message
                )
        logger.warning(
            "social_insights_failed publication=%s kind=%s", publication_id, kind
        )
        return False
    with transaction.atomic():
        state = PublicationInsights.objects.select_for_update().get(pk=state.pk)
        state.metrics = {**state.metrics, **result.values}
        state.metric_times = {
            **state.metric_times,
            **{key: now.isoformat() for key in result.values},
        }
        state.unavailable = result.unavailable
        state.checked_at, state.lease_until = now, None
        state.failures = state.failures + 1 if result.warning else 0
        state.last_error = result.warning or (
            "" if result.values else "Meta has not provided metrics for this post."
        )
        state.next_sync_at = now + timedelta(
            days=1
            if publication.published_at
            and publication.published_at > now - timedelta(days=30)
            else 7
        )
        if result.warning:
            seconds = (
                86400
                if result.retry_kind == "permission"
                else min(86400, 300 * 2 ** min(state.failures, 8))
            )
            state.next_sync_at = now + timedelta(
                seconds=seconds + random.randint(0, 60)
            )
        if result.values:
            state.synced_at = now
            InsightSnapshot.objects.update_or_create(
                publication=publication,
                day=now.date(),
                defaults={"captured_at": now, "metrics": result.values},
            )
        state.save()
        SocialAccount.objects.filter(pk=account.pk).update(
            insights_error=result.warning,
            insights_retry_after=state.next_sync_at if result.warning else None,
        )
    return True


def sync_due_insights(limit: int = 10) -> int:
    now = timezone.now()
    publications = (
        SocialPublication.objects.filter(status="published")
        .exclude(external_id="")
        .filter(
            Q(insights__isnull=True)
            | Q(insights__next_sync_at__isnull=True)
            | Q(insights__next_sync_at__lte=now)
        )
        .filter(
            Q(insights__lease_until__isnull=True) | Q(insights__lease_until__lte=now)
        )
        .order_by("insights__next_sync_at", "pk")
    )
    # Skip an entire unavailable account, so it cannot starve the other platform.
    blocked = SocialAccount.objects.filter(insights_retry_after__gt=now).values_list(
        "network", flat=True
    )
    ids = list(
        publications.exclude(network__in=blocked).values_list("pk", flat=True)[
            : max(1, min(limit, 50))
        ]
    )
    return sum(sync_publication(pk) for pk in ids)
