"""Linked Facebook promotions share the existing marketing queue and delivery path."""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.blog.models import BlogPost
from apps.social.access import can_manage_social
from apps.social.exceptions import SocialError
from apps.social.models import SocialPost
from apps.users.models import AuditLog, CustomUser


def article_url(article: BlogPost) -> str:
    base = str(settings.SOCIAL_PUBLIC_BASE_URL).rstrip("/")
    if urlparse(base).scheme != "https" or not urlparse(base).netloc:
        raise SocialError(
            "Set the public HTTPS website address before featuring articles."
        )
    return base + str(article.get_absolute_url())


def validate_promotion(
    post: SocialPost, *, when: datetime | None = None, sending: bool = False
) -> None:
    article = (
        BlogPost.objects.filter(pk=post.blog_post_id).first()
        if post.blog_post_id is not None
        else None
    )
    if article is None:
        raise SocialError(
            "The linked article was removed. This Facebook feature cannot be sent."
        )
    if not post.post_to_facebook or post.post_to_instagram:
        raise SocialError(
            "Blog features use Facebook only, with a link to the article."
        )
    if article.status != BlogPost.Status.PUBLISHED or article.published_at is None:
        raise SocialError(
            "Publish or schedule the article before scheduling its Facebook feature."
        )
    if when is not None and when < article.published_at + timedelta(minutes=1):
        raise SocialError(
            "Schedule Facebook at least one minute after the article goes live."
        )
    post.link_url = article_url(article)
    post.blog_publication_at = article.published_at
    if sending:
        if not article.is_live:
            raise SocialError(
                "The article is not public yet. Review its publication time before retrying Facebook."
            )
        try:
            response = requests.get(post.link_url, timeout=15, allow_redirects=False)
        except requests.RequestException as exc:
            raise SocialError(
                "The public article could not be reached. Facebook is held for review."
            ) from exc
        marker = f'data-blog-post-id="{article.pk}"'
        if response.status_code != 200 or marker not in response.text:
            raise SocialError(
                "The public article is unavailable. Facebook is held to avoid sharing a broken link."
            )


def sync_promotion(article: BlogPost) -> None:
    """Called after article saves, including admin edits; never resurrect a canceled share."""
    with transaction.atomic():
        promotion = (
            SocialPost.objects.select_for_update().filter(blog_post=article).first()
        )
        if promotion is None or promotion.status in {
            SocialPost.Status.POSTED,
            SocialPost.Status.PUBLISHING,
        }:
            return
        if article.status != BlogPost.Status.PUBLISHED or article.published_at is None:
            if promotion.status == SocialPost.Status.SCHEDULED:
                promotion.status = SocialPost.Status.ATTENTION
                promotion.last_error = "The article was moved back to drafts. Schedule it before retrying Facebook."
        elif promotion.status == SocialPost.Status.SCHEDULED and promotion.scheduled_at:
            old = promotion.blog_publication_at or article.published_at
            promotion.scheduled_at = max(
                promotion.scheduled_at + (article.published_at - old),
                article.published_at + timedelta(minutes=1),
            )
        # A slug change must never leave a stale outbound URL.
        if settings.SOCIAL_PUBLIC_BASE_URL:
            promotion.link_url = article_url(article)
        promotion.blog_publication_at = article.published_at
        promotion.save(
            update_fields=[
                "status",
                "last_error",
                "scheduled_at",
                "link_url",
                "blog_publication_at",
                "updated_at",
            ]
        )


def feature_article(
    article: BlogPost, *, caption: str, when: datetime, actor: CustomUser
) -> SocialPost:
    from apps.social.services import _require_publishable

    if not can_manage_social(actor):
        raise SocialError(
            "Only a current super administrator may schedule Facebook features."
        )
    if not 8 <= len(caption.strip()) <= 1800:
        raise SocialError("Write a Facebook teaser between 8 and 1,800 characters.")
    with transaction.atomic():
        locked = BlogPost.objects.select_for_update().get(pk=article.pk)
        promotion, _ = SocialPost.objects.select_for_update().get_or_create(
            blog_post=locked,
            defaults={
                "source": SocialPost.Source.BLOG,
                "created_by": actor,
                "post_to_instagram": False,
            },
        )
        if promotion.status in {SocialPost.Status.POSTED, SocialPost.Status.PUBLISHING}:
            raise SocialError(
                "This article has already been shared or is sending. Its feature cannot be scheduled twice."
            )
        if when <= timezone.now():
            raise SocialError("Choose a future Facebook time.")
        promotion.blog_auto_scheduled = False
        promotion.facebook_caption = caption.strip()
        promotion.post_to_facebook, promotion.post_to_instagram = True, False
        promotion.source = SocialPost.Source.BLOG
        validate_promotion(promotion, when=when)
        _require_publishable(promotion)
        promotion.status, promotion.scheduled_at, promotion.last_error = (
            SocialPost.Status.SCHEDULED,
            when,
            "",
        )
        promotion.save()
        AuditLog.objects.create(
            actor=actor,
            action="blog.facebook.scheduled",
            entity_type="SocialPost",
            entity_id=str(promotion.pk),
            after={"blog_post": locked.pk, "scheduled_at": when.isoformat()},
        )
    return promotion
