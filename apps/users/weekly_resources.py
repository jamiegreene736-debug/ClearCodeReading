"""Week-by-week family resources in the program’s local time zone."""

from datetime import date, datetime, time, timedelta
from typing import Any

from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.resources.models import Revision
from apps.resources.views import published_revisions
from apps.users.models import (
    ChildProfile,
    CustomUser,
    FamilyResourceWeek,
    ReaderBoardPost,
)

HOME_ACTIVITIES = (
    (
        "Talk about a picture",
        "Pick a book about something your reader enjoys. Pause at a picture and ask what they notice. Build on their answer with one new word.",
    ),
    (
        "Tell the story back",
        "After reading together, invite your reader to tell you what happened first, next and last. Look back at the pictures for clues.",
    ),
    (
        "Read a favorite again",
        "Revisit a short book your reader loves. Take turns reading familiar lines, then talk about a detail you missed the first time.",
    ),
    (
        "Make a connection",
        "Pause when a character has a problem. Ask whether it reminds your reader of something they have seen or done.",
    ),
)


def family_library() -> QuerySet[Revision]:
    return published_revisions().filter(
        audience__in=[Revision.Audience.FAMILIES, Revision.Audience.BOTH]
    )


def weekly_resources(
    user: CustomUser, readers: QuerySet[ChildProfile], page_number: str | None
) -> dict[str, Any]:
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    title, body = HOME_ACTIVITIES[(monday.toordinal() // 7) % len(HOME_ACTIVITIES)]
    edition, _ = FamilyResourceWeek.objects.get_or_create(
        week_start=monday,
        defaults={
            "activity_title": title,
            "activity_body": body,
            "library_ids": lambda: [
                str(pk)
                for pk in family_library()
                .order_by("-featured", "-created_at", "pk")
                .values_list("resource_id", flat=True)[:3]
            ],
        },
    )
    joined = timezone.localdate(user.date_joined)
    first_week = joined - timedelta(days=joined.weekday())
    older_editions = FamilyResourceWeek.objects.filter(
        week_start__gte=first_week, week_start__lt=monday
    )
    resource_posts = (
        ReaderBoardPost.objects.filter(child__in=readers)
        .exclude(resource_url="")
        .select_related("child")
    )
    start = timezone.make_aware(datetime.combine(monday, time.min))
    old_link_weeks = resource_posts.filter(created_at__lt=start).datetimes(
        "created_at", "week"
    )
    weeks = sorted(
        set(older_editions.values_list("week_start", flat=True))
        | {dt.date() for dt in old_link_weeks},
        reverse=True,
    )
    page = Paginator(weeks, 6).get_page(page_number)
    editions = older_editions.in_bulk(page.object_list)
    editions[monday] = edition
    ids = {pk for row in editions.values() for pk in row.library_ids}
    live = {
        str(revision.resource_id): revision
        for revision in family_library().filter(resource_id__in=ids)
    }
    entries: dict[date, dict[str, Any]] = {}
    for week in [monday, *page.object_list]:
        row = editions.get(week)
        entries[week] = {
            "week": week,
            "activity_title": row.activity_title if row else "",
            "activity_body": row.activity_body if row else "",
            "library": [live[pk] for pk in row.library_ids if pk in live]
            if row
            else [],
            "teacher_links": [],
        }
    ranges = Q()
    for week in entries:
        low = timezone.make_aware(datetime.combine(week, time.min))
        high = timezone.make_aware(datetime.combine(week + timedelta(days=7), time.min))
        ranges |= Q(created_at__gte=low, created_at__lt=high)
    for post in resource_posts.filter(ranges):
        posted = timezone.localdate(post.created_at)
        entries[posted - timedelta(days=posted.weekday())]["teacher_links"].append(post)
    return {
        "current_week": entries[monday],
        "earlier_weeks": [entries[week] for week in page.object_list],
        "archive_page": page,
    }
