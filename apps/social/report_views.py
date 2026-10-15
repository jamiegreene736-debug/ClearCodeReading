"""Staff-only performance pages and safe metadata updates."""

from __future__ import annotations

import csv
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.social.access import SocialRequest, social_editor_required
from apps.social.categories import apply_category, category_choices
from apps.social.exceptions import SocialError
from apps.social.insights_api import METRICS
from apps.social.models import (
    InsightSnapshot,
    PublicationInsights,
    SocialAccount,
    SocialCategory,
    SocialPost,
    SocialPublication,
)
from apps.social.reporting import (
    performance_row,
    publications_in_range,
    report_dates,
    summarize,
)


def csv_safe(value: object) -> str:
    text = str(value) if value is not None else ""
    return (
        "'" + text
        if text.lstrip().startswith(("=", "+", "-", "@"))
        or text.startswith(("\t", "\r", "\n"))
        else text
    )


@social_editor_required
@require_http_methods(["GET"])
def performance(request: SocialRequest) -> HttpResponse:
    first, last = report_dates(request.GET.get("start", ""), request.GET.get("end", ""))
    category, network = request.GET.get("category", ""), request.GET.get("network", "")
    week = request.GET.get("period") == "week"
    rows = [
        performance_row(pub, week)
        for pub in publications_in_range(first, last, category, network)
    ]
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            'attachment; filename="social-performance.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(
            [
                "Post ID",
                "Caption",
                "Category",
                "Platform",
                "Published",
                "Measurement",
                *METRICS.values(),
                "Last updated",
                "Status",
            ]
        )
        for row in rows:
            pub = row.publication
            writer.writerow(
                [
                    csv_safe(value)
                    for value in [
                        pub.post_id,
                        pub.post.headline,
                        pub.post.category.name
                        if pub.post.category
                        else "Uncategorized",
                        pub.network,
                        pub.published_at,
                        "Day 7–8 snapshot" if week else "Lifetime as last collected",
                        *[row.values.get(key) for key in METRICS],
                        row.updated,
                        row.error,
                    ]
                ]
            )
        return response
    filters = {
        "start": first.isoformat(),
        "end": last.isoformat(),
        "category": category,
        "network": network,
        "period": "week" if week else "lifetime",
    }
    return render(
        request,
        "social/performance.html",
        {
            "page": Paginator(rows, 50).get_page(request.GET.get("page")),
            "summaries": summarize(rows),
            "content_count": len({row.publication.post_id for row in rows}),
            "publication_count": len(rows),
            "categories": SocialCategory.objects.all(),
            "filters": filters,
            "filter_query": urlencode(filters),
            "accounts": SocialAccount.objects.all(),
        },
    )


@social_editor_required
@require_http_methods(["GET", "POST"])
def post_details(request: SocialRequest, pk: int) -> HttpResponse:
    post = get_object_or_404(SocialPost.objects.select_related("category"), pk=pk)
    if request.method == "POST":
        try:
            with transaction.atomic():
                post = SocialPost.objects.select_for_update().get(pk=pk)
                if request.POST.get("action") == "refresh":
                    now = timezone.now()
                    for pub in post.publications.filter(status="published"):
                        state, _ = PublicationInsights.objects.get_or_create(
                            publication=pub
                        )
                        if (
                            state.checked_at is None
                            or state.checked_at < now - timedelta(minutes=15)
                        ):
                            PublicationInsights.objects.filter(pk=state.pk).update(
                                next_sync_at=now
                            )
                    messages.success(
                        request,
                        "Refresh requested. Results will update in the background. Requests are limited to once every 15 minutes and may take longer if Meta is busy.",
                    )
                elif request.POST.get("action") == "category":
                    apply_category(post, request.POST)
                    post.save(update_fields=["category", "updated_at"])
                    messages.success(
                        request,
                        "Internal category saved. Published content is unchanged.",
                    )
                else:
                    raise SocialError("Choose a valid action.")
            return redirect("social:post_details", pk=pk)
        except SocialError as exc:
            messages.error(request, str(exc))
    publications = SocialPublication.objects.filter(
        post=post, status="published"
    ).select_related("insights")
    return render(
        request,
        "social/post_details.html",
        {
            "post": post,
            "category_choices": category_choices(post),
            "rows": [performance_row(pub) for pub in publications],
            "accounts": SocialAccount.objects.all(),
            "snapshots": InsightSnapshot.objects.filter(publication__post=post)
            .select_related("publication")
            .order_by("-captured_at")[:60],
        },
    )
