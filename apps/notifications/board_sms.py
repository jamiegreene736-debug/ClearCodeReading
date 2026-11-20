"""Verified, one-recipient Quo texts for the private reader board."""

import logging
import random
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.users.models import (
    BoardTextAccount,
    BoardTextDelivery,
    ReaderBoardNotification,
    ReaderBoardPost,
)
from apps.users.reader_board import board_readers

logger = logging.getLogger(__name__)
RELAY_NUMBER = "+14074497941"


def message_parts(post: ReaderBoardPost) -> list[str]:
    body = post.body
    if post.resource_url:
        body += "\n" + post.resource_url
    code = f"R{post.child_id}"
    label = f"ClearCode · {post.child.first_name} · {code}"
    chunks = [body[i : i + 1200] for i in range(0, len(body), 1200)]
    return [
        f"{label}"
        + (f" ({i}/{len(chunks)})" if len(chunks) > 1 else "")
        + f"\n{text}\nReply {code} followed by your note. STOP to opt out."
        for i, text in enumerate(chunks, 1)
    ]


def queue_texts(
    post: ReaderBoardPost, notifications: list[ReaderBoardNotification]
) -> None:
    verified = {
        a.user_id: a.verified_at
        for a in BoardTextAccount.objects.filter(
            user_id__in=[n.recipient_id for n in notifications],
            phone__isnull=False,
            verified_at__isnull=False,
        )
        if a.verified_at is not None
    }
    if not verified:
        return
    count = len(message_parts(post))
    deliveries = [
        BoardTextDelivery(
            notification=notice,
            part=i,
            verified_at=verified[notice.recipient_id],
        )
        for notice in notifications
        if notice.recipient_id in verified
        for i in range(count)
    ]
    BoardTextDelivery.objects.bulk_create(deliveries)


def drain_board_texts(limit: int = 5) -> None:
    if not settings.QUO_API_KEY:
        return
    now = timezone.now()
    BoardTextDelivery.objects.filter(
        status="sending", updated_at__lt=now - timedelta(minutes=5)
    ).update(status=BoardTextDelivery.Status.UNCERTAIN, updated_at=now)
    ids = (
        BoardTextDelivery.objects.filter(status="queued", next_attempt__lte=now)
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    for pk in list(ids):
        send_board_text(pk)


def send_board_text(pk: int) -> None:
    with transaction.atomic():
        delivery = (
            BoardTextDelivery.objects.select_for_update(of=("self",))
            .select_related(
                "notification__recipient",
                "notification__post__child",
                "notification__post__author",
            )
            .get(pk=pk)
        )
        if (
            delivery.status != BoardTextDelivery.Status.QUEUED
            or delivery.next_attempt > timezone.now()
        ):
            return
        notice = delivery.notification
        post = notice.post
        account = BoardTextAccount.objects.filter(
            user=notice.recipient, verified_at=delivery.verified_at
        ).first()
        if (
            not account
            or not account.phone
            or not board_readers(notice.recipient).filter(pk=post.child_id).exists()
            or not board_readers(post.author).filter(pk=post.child_id).exists()
        ):
            delivery.status = BoardTextDelivery.Status.CANCELLED
            delivery.save(update_fields=["status", "updated_at"])
            return
        destination = account.phone
        content = message_parts(post)[delivery.part]
        delivery.attempts += 1
        delivery.status = BoardTextDelivery.Status.SENDING
        delivery.save(update_fields=["attempts", "status", "updated_at"])
    # No Quo idempotency key is documented. Retry only explicit rate-limit rejections.
    status = BoardTextDelivery.Status.UNCERTAIN
    provider_id = ""
    next_attempt = timezone.now()
    try:
        response = requests.post(
            "https://api.quo.com/v1/messages",
            headers={"Authorization": settings.QUO_API_KEY},
            json={"from": RELAY_NUMBER, "to": [destination], "content": content},
            timeout=(3, 8),
            allow_redirects=False,
        )
        if response.status_code == 429 and delivery.attempts < 7:
            retry = response.headers.get("Retry-After", "")
            delay = max(
                2**delivery.attempts,
                min(int(retry), 86400)
                if retry.isascii() and retry.isdigit() and len(retry) < 8
                else 0,
            )
            next_attempt += timedelta(seconds=delay + random.uniform(0, 3))
            status = BoardTextDelivery.Status.QUEUED
        elif 400 <= response.status_code < 500:
            status = BoardTextDelivery.Status.FAILED
        elif 200 <= response.status_code < 300:
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if (
                isinstance(data, dict)
                and isinstance(data.get("id"), str)
                and 0 < len(data["id"]) <= 120
            ):
                provider_id = data["id"]
                status = BoardTextDelivery.Status.ACCEPTED
    except (requests.RequestException, ValueError):
        pass  # A timeout or unreadable provider receipt cannot establish whether Quo sent the text.
    BoardTextDelivery.objects.filter(
        pk=pk, status=BoardTextDelivery.Status.SENDING
    ).update(
        status=status,
        provider_id=provider_id,
        next_attempt=next_attempt,
        updated_at=timezone.now(),
    )
    if status in {BoardTextDelivery.Status.FAILED, BoardTextDelivery.Status.UNCERTAIN}:
        logger.warning("reader_sms_delivery id=%s status=%s", pk, status)
