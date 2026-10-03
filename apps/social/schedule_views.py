"""Calendar presentation and confirmed removal of unpublished social posts."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from typing import TypedDict

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.social.access import SocialRequest, social_editor_required
from apps.social.exceptions import SocialError
from apps.social.models import ContentWeek, SocialPost
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


def calendar_days(focus: date, today: date) -> list[list[CalendarDay]]:
    days = calendar.Calendar(firstweekday=calendar.SUNDAY).monthdatescalendar(
        focus.year, focus.month
    )
    start = datetime.combine(days[0][0], time.min, EASTERN)
    end = datetime.combine(days[-1][-1] + timedelta(days=1), time.min, EASTERN)
    posts = (
        SocialPost.objects.filter(scheduled_at__gte=start, scheduled_at__lt=end)
        .exclude(status=SocialPost.Status.DRAFT)
        .select_related("blog_post")
        .defer("image_data", "blog_post__cover_data", "blog_post__body")
        .prefetch_related("publications")
        .order_by("scheduled_at", "pk")
    )
    grouped: dict[date, list[SocialPost]] = {}
    for post in posts:
        if post.scheduled_at is not None:
            grouped.setdefault(post.scheduled_at.astimezone(EASTERN).date(), []).append(
                post
            )
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
    return render(
        request,
        "social/calendar.html",
        {
            "focus": focus,
            "previous": previous.strftime("%Y-%m"),
            "following": following.strftime("%Y-%m"),
            "today_month": today.strftime("%Y-%m"),
            "weeks": calendar_days(focus, today),
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
        return redirect(
            f"{reverse('social:queue')}?tab={'drafts' if post.status == SocialPost.Status.DRAFT else 'scheduled'}"
        )
    if post.status not in {SocialPost.Status.DRAFT, SocialPost.Status.SCHEDULED}:
        messages.error(request, "Only drafts and scheduled posts can be deleted.")
        return redirect("social:queue")
    return render(
        request,
        "social/delete.html",
        {"post": post, "ai_planned": ContentWeek.objects.filter(post=post).exists()},
    )
