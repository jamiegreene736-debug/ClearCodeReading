"""Durable weekly planning, deliberately separate from public publishing."""

from __future__ import annotations

import logging
import random
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from apps.blog.models import BlogContentPlan as ContentPlan
from apps.blog.models import BlogContentWeek as ContentWeek
from apps.blog.models import BlogPost, clean_article_html
from apps.blog.planner_ai import generate_article, render_body, review_article
from apps.blog.promotion import article_url, validate_promotion
from apps.social.access import can_manage_social
from apps.social.ai import ai_configured, generate_image
from apps.social.editorial import PILLAR_DIRECTIONS, pillar_for, source_context
from apps.social.exceptions import SocialError
from apps.social.models import SocialPost
from apps.social.planner import upcoming_slots
from apps.social.services import _require_publishable, connected_account
from apps.users.models import AuditLog, CustomUser

logger = logging.getLogger(__name__)
PLANNER_LOCK = 491730219
PREVIEW_WEEKS = 4
MAX_ATTEMPTS = 3


def get_plan() -> ContentPlan:
    plan, _ = ContentPlan.objects.get_or_create(pk=1)
    return plan


def validate_plan(plan: ContentPlan) -> None:
    if not ai_configured():
        raise SocialError("AI generation is not configured on the server.")
    if plan.updated_by is None or not can_manage_social(plan.updated_by):
        raise SocialError("A current super administrator must save this plan.")
    if (
        plan.mode == ContentPlan.Mode.AUTOMATIC
        and plan.promote_facebook
        and connected_account("facebook") is None
    ):
        raise SocialError(
            "Connect Facebook before enabling automatic article features."
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
            "promote_facebook",
            "promotion_delay_hours",
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
            for week in (
                ContentWeek.objects.select_for_update(of=("self",))
                .filter(auto_scheduled=True)
                .select_related("post")
            ):
                if week.post and week.post.is_scheduled:
                    week.post.status = BlogPost.Status.DRAFT
                    week.post.save(update_fields=["status", "updated_at"])
                SocialPost.objects.filter(
                    blog_post_id=week.post_id,
                    blog_auto_scheduled=True,
                    status__in=[
                        SocialPost.Status.SCHEDULED,
                        SocialPost.Status.ATTENTION,
                    ],
                ).update(
                    status=SocialPost.Status.DRAFT, scheduled_at=None, last_error=""
                )
                week.auto_scheduled = False
                week.status = ContentWeek.Status.HELD
                week.review_note = "Automatic publication cancelled. Review the article and schedule it again when ready."
                week.save()
        AuditLog.objects.create(
            actor=actor,
            action="blog.plan.updated",
            entity_type="BlogContentPlan",
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
            post = BlogPost.objects.select_for_update().get(pk=week.post_id)
            promotion = (
                SocialPost.objects.select_for_update().filter(blog_post=post).first()
            )
            if post.is_live or (
                promotion
                and promotion.status
                in {SocialPost.Status.POSTED, SocialPost.Status.PUBLISHING}
            ):
                raise SocialError(
                    "This article or its Facebook feature is already publishing or published."
                )
            post.status = BlogPost.Status.DRAFT
            post.save(update_fields=["status", "updated_at"])
            if promotion:
                promotion.status, promotion.scheduled_at, promotion.last_error = (
                    SocialPost.Status.DRAFT,
                    None,
                    "",
                )
                promotion.save()
        week.status, week.auto_scheduled = ContentWeek.Status.SKIPPED, False
        week.save(update_fields=["status", "auto_scheduled", "updated_at"])
        AuditLog.objects.create(
            actor=actor,
            action="blog.plan.skipped",
            entity_type="BlogContentWeek",
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
        "recent_titles": list(
            BlogPost.objects.order_by("-created_at").values_list("title", flat=True)[
                :24
            ]
        ),
        "promote_facebook": plan.promote_facebook,
        "promotion_delay_hours": plan.promotion_delay_hours,
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


def schedule_week(pk: int, *, actor: CustomUser, automatic: bool = False) -> None:
    with transaction.atomic():
        plan = ContentPlan.objects.select_for_update().get(pk=1)
        if not can_manage_social(actor):
            raise SocialError("A current super administrator must schedule articles.")
        if automatic:
            if plan.mode != ContentPlan.Mode.AUTOMATIC:
                return
            validate_plan(plan)
        week = ContentWeek.objects.select_for_update().get(pk=pk)
        if (
            week.status != ContentWeek.Status.READY
            or not week.review_passed
            or not week.post_id
        ):
            raise SocialError(
                "Only an approved, ready article can be scheduled here. Edit other articles in the blog editor."
            )
        post = BlogPost.objects.select_for_update().get(pk=week.post_id)
        if post.status != BlogPost.Status.DRAFT:
            raise SocialError(
                "This article has already been scheduled or published in the editor."
            )
        if week.planned_at <= timezone.now():
            raise SocialError(
                "The planned time passed. Choose a new date in the article editor."
            )
        if (
            post.title != str(week.content["title"])
            or post.body != clean_article_html(render_body(week.content))
            or post.excerpt != str(week.content["excerpt"])
        ):
            raise SocialError(
                "The article was edited after its AI check. Review and schedule it in the article editor."
            )
        post.status, post.published_at = BlogPost.Status.PUBLISHED, week.planned_at
        post.save()
        promotion = (
            SocialPost.objects.select_for_update().filter(blog_post=post).first()
        )
        if promotion:
            if promotion.status != SocialPost.Status.DRAFT:
                raise SocialError(
                    "The Facebook feature has already been changed. Review it in the social schedule."
                )
            if promotion.facebook_caption != str(week.content["facebook"]):
                raise SocialError(
                    "The teaser was edited after its AI check. Review and schedule it from Feature on Facebook."
                )
            when = week.planned_at + timedelta(
                hours=int(week.context.get("promotion_delay_hours", 1))
            )
            validate_promotion(promotion, when=when)
            _require_publishable(promotion)
            promotion.blog_auto_scheduled = automatic
            promotion.status, promotion.scheduled_at = SocialPost.Status.SCHEDULED, when
            promotion.save()
        week.status, week.auto_scheduled = ContentWeek.Status.SCHEDULED, automatic
        week.save()
        AuditLog.objects.create(
            actor=actor,
            action="blog.plan.scheduled",
            entity_type="BlogContentWeek",
            entity_id=str(pk),
            after={"automatic": automatic, "published_at": week.planned_at.isoformat()},
        )


def _schedule_ready(plan: ContentPlan) -> None:
    if plan.mode != ContentPlan.Mode.AUTOMATIC or plan.updated_by is None:
        return
    for week in ContentWeek.objects.filter(
        status=ContentWeek.Status.READY, review_passed=True
    ):
        try:
            schedule_week(week.pk, actor=plan.updated_by, automatic=True)
        except SocialError as exc:
            ContentWeek.objects.filter(
                pk=week.pk, status=ContentWeek.Status.READY
            ).update(status=ContentWeek.Status.HELD, review_note=str(exc)[:500])


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
        week.content = generate_article(week.context)
        ContentWeek.objects.filter(pk=week.pk).update(content=week.content)
    if ContentWeek.objects.filter(
        pk=week.pk, status=ContentWeek.Status.SKIPPED
    ).exists():
        return
    if not week.review_note:
        week.review_passed, week.review_note = review_article(
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
        locked.post = BlogPost.objects.create(
            title=str(week.content["title"]),
            excerpt=str(week.content["excerpt"]),
            body=render_body(week.content),
            body_format=BlogPost.BodyFormat.HTML,
            category="Reading support",
            seo_title=str(week.content["seo_title"]),
            seo_description=str(week.content["seo_description"]),
            cover_data=raw or None,
            cover_content_type=content_type,
            cover_image_alt=str(week.content["cover_alt"]) if raw else "",
            author=current.updated_by,
        )
        if week.context.get("promote_facebook"):
            SocialPost.objects.create(
                blog_post=locked.post,
                source=SocialPost.Source.BLOG,
                facebook_caption=str(week.content["facebook"]),
                post_to_facebook=True,
                post_to_instagram=False,
                link_url=article_url(locked.post),
                created_by=current.updated_by,
            )
        locked.status = (
            ContentWeek.Status.READY if week.review_passed else ContentWeek.Status.HELD
        )
        locked.last_error, locked.next_attempt_at = "", None
        locked.save()
        AuditLog.objects.create(
            actor=current.updated_by,
            action="blog.plan.generated",
            entity_type="BlogContentWeek",
            entity_id=str(week.pk),
            after={"review_passed": week.review_passed, "attempts": week.attempts},
        )
    logger.info(
        "blog_plan_generated week=%s attempt=%s approved=%s image_bytes=%s",
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
            "blog_plan_generation_failed week=%s attempt=%s", week.pk, week.attempts
        )
        return 0
    _schedule_ready(ContentPlan.objects.get(pk=1))
    ContentPlan.objects.filter(pk=1).update(last_error="")
    return 1


def maintain_blog_plan() -> int:
    """Generate at most one week's image per pass; serialize cron and Celery workers."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [PLANNER_LOCK])
        if not cursor.fetchone()[0]:
            return 0
    try:
        return _run_locked()
    except SocialError as exc:
        ContentPlan.objects.filter(pk=1).update(last_error=str(exc)[:300])
        logger.warning("blog_plan_not_ready")
        return 0
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [PLANNER_LOCK])
