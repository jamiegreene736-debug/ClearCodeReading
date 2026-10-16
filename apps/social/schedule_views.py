"""Calendar presentation and confirmed removal of unpublished social posts."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from typing import TypedDict, cast
from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Min
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.social.access import SocialRequest, social_editor_required
from apps.social.exceptions import SocialError
from apps.social.models import ContentWeek, SocialCategory, SocialPost
from apps.social.navigation import navigation
from apps.social.services import EASTERN, delete_unpublished_post


class CalendarDay(TypedDict):
    date: date
    in_month: bool
    today: bool
    posts: list[SocialPost]


def month_start(value: str, today: date) -> date:
    try:
        parsed = date.fromisoformat(value + "-01")
    except ValueError:
        return today.replace(day=1)
    # Leave room for adjacent months and for Eastern/UTC conversions at year boundaries.
    return parsed if 1901 <= parsed.year <= 9998 else today.replace(day=1)


def calendar_days(
    focus: date, today: date, category: str = "", network: str = "", status: str = ""
) -> list[list[CalendarDay]]:
    days = calendar.Calendar(firstweekday=calendar.SUNDAY).monthdatescalendar(
        focus.year, focus.month
    )
    start = datetime.combine(days[0][0], time.min, EASTERN)
    end = datetime.combine(days[-1][-1] + timedelta(days=1), time.min, EASTERN)
    posts = (
        SocialPost.objects.annotate(
            calendar_at=Coalesce("scheduled_at", Min("publications__published_at"))
        )
        .filter(calendar_at__gte=start, calendar_at__lt=end)
        .exclude(status=SocialPost.Status.DRAFT)
        .select_related("blog_post", "category")
        .defer("video_data", "image_data", "blog_post__cover_data", "blog_post__body")
        .prefetch_related("publications")
        .order_by("calendar_at", "pk")
    )
    if category == "none":
        posts = posts.filter(category__isnull=True)
    elif category.isdecimal() and len(category) <= 18:
        posts = posts.filter(category_id=int(category))
    if network in {"facebook", "instagram"}:
        posts = posts.filter(**{f"post_to_{network}": True})
    if status in SocialPost.Status.values:
        posts = posts.filter(status=status)
    grouped: dict[date, list[SocialPost]] = {}
    for post in posts:
        at = cast(datetime, post.__dict__["calendar_at"])
        grouped.setdefault(at.astimezone(EASTERN).date(), []).append(post)
    return [
        [
            {
                "date": day,
                "in_month": day.month == focus.month,
                "today": day == today,
                "posts": grouped.get(day, []),
            }
            for day in week
        ]
        for week in days
    ]


@social_editor_required
@require_http_methods(["GET"])
def schedule_calendar(request: SocialRequest) -> HttpResponse:
    today = timezone.now().astimezone(EASTERN).date()
    focus = month_start(request.GET.get("month", ""), today)
    previous = focus - timedelta(days=1)
    following = (focus + timedelta(days=32)).replace(day=1)
    category, network, status = (
        request.GET.get(key, "") for key in ("category", "network", "status")
    )
    weeks = calendar_days(focus, today, category, network, status)
    totals: dict[str, dict[str, int | str]] = {}
    for week in weeks:
        for day in week:
            if not day["in_month"]:
                continue
            for post in day["posts"]:
                label = post.category.name if post.category else "Uncategorized"
                row = totals.setdefault(
                    label, {"name": label, "posted": 0, "scheduled": 0}
                )
                if post.status in {"posted", "scheduled"}:
                    row[post.status] = int(row[post.status]) + 1
    filters = {"category": category, "network": network, "status": status}
    return render(
        request,
        "social/calendar.html",
        {
            "focus": focus,
            "previous": previous.strftime("%Y-%m"),
            "following": following.strftime("%Y-%m"),
            "today_month": today.strftime("%Y-%m"),
            "weeks": weeks,
            "categories": SocialCategory.objects.all(),
            "selected_category": category,
            "selected_network": network,
            "selected_status": status,
            "statuses": SocialPost.Status.choices,
            "summaries": list(totals.values()),
            "filter_query": urlencode(filters),
            "weekdays": ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"),
        },
    )


@social_editor_required
@require_http_methods(["GET", "POST"])
def delete_post(request: SocialRequest, pk: int) -> HttpResponse:
    post = get_object_or_404(SocialPost, pk=pk)
    if request.method == "POST":
        try:
            delete_unpublished_post(pk, actor=request.user)
        except SocialError as exc:
            messages.error(request, str(exc))
            return redirect("social:queue")
        messages.success(
            request, "Post deleted. It will not publish or be recreated by the AI plan."
        )
        return redirect(navigation(request, post)["url"])
    if post.status not in {SocialPost.Status.DRAFT, SocialPost.Status.SCHEDULED}:
        messages.error(request, "Only drafts and scheduled posts can be deleted.")
        return redirect("social:queue")
    return render(
        request,
        "social/delete.html",
        {"post": post, "ai_planned": ContentWeek.objects.filter(post=post).exists()},
    )
