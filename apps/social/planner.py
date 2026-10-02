"""Durable weekly planning, deliberately separate from public publishing."""

from __future__ import annotations

import logging
import random
from datetime import date, datetime, time, timedelta
from typing import cast

from django.db import connection, transaction
from django.utils import timezone

from apps.social.access import can_manage_social
from apps.social.ai import ai_configured, generate_image
from apps.social.editorial import CTA_URL, PILLAR_DIRECTIONS, pillar_for, source_context
from apps.social.exceptions import SocialError
from apps.social.models import ContentPlan, ContentWeek, SocialPost
from apps.social.planner_ai import generate_content, review_content
from apps.social.services import EASTERN, connected_account
from apps.users.models import AuditLog, CustomUser

logger = logging.getLogger(__name__)
PLANNER_LOCK = 491730218
PREVIEW_WEEKS = 4
MAX_ATTEMPTS = 3


def get_plan() -> ContentPlan:
    plan, _ = ContentPlan.objects.get_or_create(pk=1)
    return plan


def upcoming_slots(plan: ContentPlan, now: datetime) -> list[tuple[date, datetime]]:
    local = now.astimezone(EASTERN)
    monday = local.date() - timedelta(days=local.weekday())
    result: list[tuple[date, datetime]] = []
    while len(result) < PREVIEW_WEEKS:
        target = datetime.combine(
            monday + timedelta(days=plan.weekday), time(plan.posting_hour), EASTERN
        )
        # Every newly generated post has at least a day available for preview.
        if target >= now + timedelta(hours=24):
            result.append((monday, target))
        monday += timedelta(days=7)
    return result


def validate_plan(plan: ContentPlan) -> None:
    if not plan.post_to_facebook and not plan.post_to_instagram:
        raise SocialError("Choose Facebook, Instagram, or both.")
    if not ai_configured():
        raise SocialError("AI generation is not configured on the server.")
    if plan.updated_by is None or not can_manage_social(plan.updated_by):
        raise SocialError("A current super administrator must save this plan.")
    if plan.mode == ContentPlan.Mode.AUTOMATIC:
        for network, selected in (
            ("facebook", plan.post_to_facebook),
            ("instagram", plan.post_to_instagram),
        ):
            if selected and connected_account(network) is None:
                raise SocialError(
                    f"Connect {network.title()} before enabling automatic posts."
                )


def save_plan(*, values: dict[str, object], actor: CustomUser) -> ContentPlan:
    get_plan()
    with transaction.atomic():
        plan = ContentPlan.objects.select_for_update().get(pk=1)
        for key in (
            "mode",
            "weekday",
            "posting_hour",
            "audience",
            "priorities",
            "post_to_facebook",
            "post_to_instagram",
        ):
            setattr(plan, key, values[key])
        plan.updated_by = actor
        if plan.mode != ContentPlan.Mode.PAUSED:
            validate_plan(plan)
        plan.last_error = ""
        if plan.mode == ContentPlan.Mode.PAUSED:
            plan.preview_requested = False
        plan.save()
        if plan.mode != ContentPlan.Mode.AUTOMATIC:
            ids = ContentWeek.objects.filter(auto_scheduled=True).values("post_id")
            SocialPost.objects.filter(
                pk__in=ids, status=SocialPost.Status.SCHEDULED
            ).update(status=SocialPost.Status.DRAFT, scheduled_at=None)
            ContentWeek.objects.filter(auto_scheduled=True).update(auto_scheduled=False)
        AuditLog.objects.create(
            actor=actor,
            action="marketing.plan.updated",
            entity_type="ContentPlan",
            entity_id="1",
            after={"mode": plan.mode},
        )
    return plan


def request_preview(*, actor: CustomUser) -> None:
    plan = get_plan()
    plan.updated_by = actor
    validate_plan(plan)
    ContentPlan.objects.filter(pk=1).update(
        preview_requested=True, updated_by=actor, last_error=""
    )


def skip_week(pk: int, *, actor: CustomUser) -> None:
    with transaction.atomic():
        ContentPlan.objects.select_for_update().get(pk=1)
        week = ContentWeek.objects.select_for_update().get(pk=pk)
        if week.post_id:
            post = SocialPost.objects.select_for_update().get(pk=week.post_id)
            if post.status in {SocialPost.Status.POSTED, SocialPost.Status.PUBLISHING}:
                raise SocialError("This post is already sending or posted.")
            post.status, post.scheduled_at = SocialPost.Status.DRAFT, None
            post.save(update_fields=["status", "scheduled_at", "updated_at"])
        week.status, week.auto_scheduled = ContentWeek.Status.SKIPPED, False
        week.save(update_fields=["status", "auto_scheduled", "updated_at"])
        AuditLog.objects.create(
            actor=actor,
            action="marketing.plan.skipped",
            entity_type="ContentWeek",
            entity_id=str(pk),
        )


def retry_week(pk: int, *, actor: CustomUser) -> None:
    with transaction.atomic():
        plan = ContentPlan.objects.select_for_update().get(pk=1)
        week = ContentWeek.objects.select_for_update().get(pk=pk)
        if week.status != ContentWeek.Status.ERROR or week.post_id:
            raise SocialError("Only a failed generation can be retried here.")
        if week.planned_at <= timezone.now():
            raise SocialError("This week has passed. Prepare upcoming weeks instead.")
        week.status, week.attempts, week.next_attempt_at, week.last_error = (
            ContentWeek.Status.PENDING,
            0,
            None,
            "",
        )
        week.save()
        plan.preview_requested, plan.updated_by = True, actor
        plan.save(update_fields=["preview_requested", "updated_by", "updated_at"])


def _context(plan: ContentPlan, week: ContentWeek) -> dict[str, object]:
    return {
        "approved_sources": source_context(),
        "week_of": week.week_of.isoformat(),
        "pillar": week.pillar,
        "editorial_direction": PILLAR_DIRECTIONS[week.pillar],
        "audience": plan.audience,
        "priorities": plan.priorities,
        "recent_captions": list(
            SocialPost.objects.exclude(facebook_caption="")
            .order_by("-created_at")
            .values_list("facebook_caption", flat=True)[:16]
        ),
        "networks": [
            network
            for network, selected in (
                ("facebook", plan.post_to_facebook),
                ("instagram", plan.post_to_instagram),
            )
            if selected
        ],
    }


def _prepare_weeks(plan: ContentPlan) -> None:
    now = timezone.now()
    # Preserve a preview already due within a day instead of adding a fifth future week.
    imminent = ContentWeek.objects.filter(
        planned_at__gt=now, planned_at__lt=now + timedelta(hours=24)
    ).count()
    for monday, planned_at in upcoming_slots(plan, now)[
        : max(0, PREVIEW_WEEKS - imminent)
    ]:
        week, created = ContentWeek.objects.get_or_create(
            week_of=monday,
            defaults={"planned_at": planned_at, "pillar": pillar_for(monday)},
        )
        if (
            not created
            and week.status == ContentWeek.Status.PENDING
            and not week.context
        ):
            ContentWeek.objects.filter(
                pk=week.pk, status=ContentWeek.Status.PENDING
            ).update(planned_at=planned_at)


def _schedule_ready(plan: ContentPlan) -> None:
    if plan.mode != ContentPlan.Mode.AUTOMATIC:
        return
    for week_id in ContentWeek.objects.filter(
        status=ContentWeek.Status.READY,
        review_passed=True,
        post__status=SocialPost.Status.DRAFT,
    ).values_list("pk", flat=True):
        with transaction.atomic():
            current = ContentPlan.objects.select_for_update().get(pk=1)
            if current.mode != ContentPlan.Mode.AUTOMATIC:
                return
            validate_plan(current)
            week = ContentWeek.objects.select_for_update().get(pk=week_id)
            if week.post_id is None:
                continue
            post = SocialPost.objects.select_for_update().get(pk=week.post_id)
            if (
                week.status != ContentWeek.Status.READY
                or post.status != SocialPost.Status.DRAFT
            ):
                continue
            if week.planned_at <= timezone.now():
                week.status, week.review_note = (
                    ContentWeek.Status.HELD,
                    "The planned time passed. Review and choose a new date.",
                )
                week.save(update_fields=["status", "review_note", "updated_at"])
                continue
            from apps.social.services import _require_publishable

            _require_publishable(post)
            post.status, post.scheduled_at = (
                SocialPost.Status.SCHEDULED,
                week.planned_at,
            )
            post.save(update_fields=["status", "scheduled_at", "updated_at"])
            week.auto_scheduled = True
            week.save(update_fields=["auto_scheduled", "updated_at"])
            AuditLog.objects.create(
                actor=current.updated_by,
                action="marketing.plan.scheduled",
                entity_type="SocialPost",
                entity_id=str(post.pk),
                after={"scheduled_at": week.planned_at.isoformat()},
            )


def _generate_week(plan: ContentPlan, week: ContentWeek) -> None:
    with transaction.atomic():
        current = ContentPlan.objects.select_for_update().get(pk=1)
        locked = ContentWeek.objects.select_for_update().get(pk=week.pk)
        if locked.status != ContentWeek.Status.PENDING or (
            current.mode == ContentPlan.Mode.PAUSED and not current.preview_requested
        ):
            return
        locked.status = ContentWeek.Status.GENERATING
        locked.attempts += 1
        if not locked.context:
            locked.context = _context(current, locked)
        locked.save()
        week.refresh_from_db()
    if not week.content:
        week.content = generate_content(week.context)
        ContentWeek.objects.filter(pk=week.pk).update(content=week.content)
    if ContentWeek.objects.filter(
        pk=week.pk, status=ContentWeek.Status.SKIPPED
    ).exists():
        return
    if not week.review_note:
        week.review_passed, week.review_note = review_content(
            week.content, week.context
        )
        ContentWeek.objects.filter(pk=week.pk).update(
            review_passed=week.review_passed, review_note=week.review_note
        )
    # Failed editorial checks remain visible as text drafts; do not spend on an image.
    raw, content_type = (
        generate_image(subject=str(week.content["image_brief"]))
        if week.review_passed
        else (b"", "")
    )
    with transaction.atomic():
        current = ContentPlan.objects.select_for_update().get(pk=1)
        locked = ContentWeek.objects.select_for_update().get(pk=week.pk)
        if locked.status == ContentWeek.Status.SKIPPED or locked.post_id:
            return
        networks = cast(list[str], week.context["networks"])
        facebook = str(week.content["facebook"])
        link = CTA_URL if week.pillar == "clearcode_approach" else ""
        if link:
            facebook += "\n\n" + link
        locked.post = SocialPost.objects.create(
            brief=str(week.content["brief"]),
            facebook_caption=facebook,
            instagram_caption=str(week.content["instagram"]),
            audience=str(week.context["audience"]),
            tone=SocialPost.Tone.WARM,
            source=SocialPost.Source.BRIEF,
            link_url=link,
            post_to_facebook="facebook" in networks,
            post_to_instagram="instagram" in networks,
            image_data=raw or None,
            image_content_type=content_type,
            image_name="weekly-illustration.jpg" if raw else "",
            created_by=current.updated_by,
        )
        locked.status = (
            ContentWeek.Status.READY if week.review_passed else ContentWeek.Status.HELD
        )
        locked.last_error, locked.next_attempt_at = "", None
        locked.save()
        AuditLog.objects.create(
            actor=current.updated_by,
            action="marketing.plan.generated",
            entity_type="ContentWeek",
            entity_id=str(week.pk),
            after={"review_passed": week.review_passed, "attempts": week.attempts},
        )
    logger.info(
        "social_plan_generated week=%s attempt=%s approved=%s image_bytes=%s",
        week.pk,
        week.attempts,
        week.review_passed,
        len(raw),
    )


def _run_locked() -> int:
    plan = get_plan()
    ContentPlan.objects.filter(pk=1).update(last_worker_at=timezone.now())
    if plan.mode == ContentPlan.Mode.PAUSED and not plan.preview_requested:
        return 0
    validate_plan(plan)
    _prepare_weeks(plan)
    _schedule_ready(plan)
    # An interrupted process cannot hold this advisory lock. Its unfinished slot is recoverable.
    ContentWeek.objects.filter(status=ContentWeek.Status.GENERATING).update(
        status=ContentWeek.Status.PENDING
    )
    ContentWeek.objects.filter(
        status=ContentWeek.Status.PENDING, planned_at__lte=timezone.now()
    ).update(
        status=ContentWeek.Status.HELD,
        review_note="The planned time passed before generation finished.",
    )
    pending = ContentWeek.objects.filter(
        status=ContentWeek.Status.PENDING, planned_at__gt=timezone.now()
    ).order_by("planned_at")
    week = next(
        (
            item
            for item in pending
            if item.next_attempt_at is None or item.next_attempt_at <= timezone.now()
        ),
        None,
    )
    if week is None:
        if not pending.exists():
            ContentPlan.objects.filter(pk=1).update(preview_requested=False)
        return 0
    if week.attempts >= MAX_ATTEMPTS:
        ContentWeek.objects.filter(pk=week.pk).update(
            status=ContentWeek.Status.ERROR,
            last_error="Generation was interrupted repeatedly. Retry this week when ready.",
        )
        return 0
    try:
        _generate_week(plan, week)
    except SocialError as exc:
        delay = timedelta(minutes=5 * 2**week.attempts, seconds=random.uniform(0, 60))
        ContentWeek.objects.filter(pk=week.pk).exclude(
            status=ContentWeek.Status.SKIPPED
        ).update(
            status=ContentWeek.Status.ERROR
            if week.attempts >= MAX_ATTEMPTS
            else ContentWeek.Status.PENDING,
            last_error=str(exc)[:300],
            next_attempt_at=timezone.now() + delay,
        )
        ContentPlan.objects.filter(pk=1).update(last_error=str(exc)[:300])
        logger.warning(
            "social_plan_generation_failed week=%s attempt=%s", week.pk, week.attempts
        )
        return 0
    _schedule_ready(ContentPlan.objects.get(pk=1))
    ContentPlan.objects.filter(pk=1).update(last_error="")
    return 1


def maintain_content_plan() -> int:
    """Generate at most one week's image per pass; serialize cron and Celery workers."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [PLANNER_LOCK])
        if not cursor.fetchone()[0]:
            return 0
    try:
        return _run_locked()
    except SocialError as exc:
        ContentPlan.objects.filter(pk=1).update(last_error=str(exc)[:300])
        logger.warning("social_plan_not_ready")
        return 0
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [PLANNER_LOCK])
