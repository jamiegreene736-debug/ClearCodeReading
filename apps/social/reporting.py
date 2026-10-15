"""Comparable, coverage-aware per-platform performance summaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db.models import F, Prefetch, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.social.insights_api import METRICS, count
from apps.social.models import InsightSnapshot, PublicationInsights, SocialPublication
from apps.social.services import EASTERN


@dataclass
class MetricCell:
    label: str
    value: int | None
    updated: datetime | None = None
    note: str = ""


@dataclass
class PerformanceRow:
    publication: SocialPublication
    cells: list[MetricCell]
    values: dict[str, int]
    updated: datetime | None
    error: str


@dataclass
class CategorySummary:
    name: str
    network: str
    posts: int = 0
    totals: dict[str, int] = field(default_factory=dict)
    coverage: dict[str, int] = field(default_factory=dict)
    latest: datetime | None = None
    best: PerformanceRow | None = None

    @property
    def average_views(self) -> str:
        n = self.coverage.get("views", 0)
        return f"{self.totals.get('views', 0) / n:,.0f}" if n else "Unavailable"


def report_dates(start: str, end: str) -> tuple[date, date]:
    today = timezone.now().astimezone(EASTERN).date()
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return today - timedelta(days=29), today
    if first > last or (last - first).days > 366 or first.year < 1901 or last > today:
        return today - timedelta(days=29), today
    return first, last


def publications_in_range(
    first: date, last: date, category: str, network: str
) -> QuerySet[SocialPublication]:
    publications = (
        SocialPublication.objects.filter(
            status="published",
            published_at__gte=datetime.combine(first, time.min, EASTERN),
            published_at__lt=datetime.combine(
                last + timedelta(days=1), time.min, EASTERN
            ),
        )
        .select_related("post__category", "insights")
        .defer("post__image_data")
        .order_by("-published_at", "pk")
    )
    if category == "none":
        publications = publications.filter(post__category__isnull=True)
    elif category.isdecimal() and len(category) <= 18:
        publications = publications.filter(post__category_id=int(category))
    if network in {"facebook", "instagram"}:
        publications = publications.filter(network=network)
    snapshots = InsightSnapshot.objects.filter(
        captured_at__gte=F("publication__published_at") + timedelta(days=7),
        captured_at__lte=F("publication__published_at") + timedelta(days=8),
    ).order_by("captured_at")
    return publications.prefetch_related(
        Prefetch("insight_snapshots", queryset=snapshots, to_attr="week_snapshots")
    )


def performance_row(
    publication: SocialPublication, week: bool = False
) -> PerformanceRow:
    state: PublicationInsights | None = getattr(publication, "insights", None)
    raw = state.metrics if state else {}
    updated = state.synced_at if state else None
    error = state.last_error if state else "Not yet collected."
    unavailable = state.unavailable if state else {}
    times = state.metric_times if state else {}
    if week:
        snapshots: list[InsightSnapshot] = getattr(publication, "week_snapshots", [])
        snapshot = snapshots[0] if snapshots else None
        raw = snapshot.metrics if snapshot else {}
        updated = snapshot.captured_at if snapshot else None
        error = "" if snapshot else "No snapshot was collected between days 7 and 8."
        unavailable, times = {}, {}
    values = {
        key: number for key in METRICS if (number := count(raw.get(key))) is not None
    }
    cells = [
        MetricCell(
            label,
            values.get(key),
            parse_datetime(times[key]) if key in times else None,
            unavailable.get(key, "") if state else "Not yet collected.",
        )
        for key, label in METRICS.items()
    ]
    return PerformanceRow(publication, cells, values, updated, error)


def summarize(rows: list[PerformanceRow]) -> list[CategorySummary]:
    groups: dict[tuple[int | None, str], CategorySummary] = {}
    for row in rows:
        post, pub = row.publication.post, row.publication
        key = (post.category_id, pub.network)
        summary = groups.setdefault(
            key,
            CategorySummary(
                post.category.name if post.category else "Uncategorized",
                pub.get_network_display(),
            ),
        )
        summary.posts += 1
        if pub.published_at and (
            summary.latest is None or pub.published_at > summary.latest
        ):
            summary.latest = pub.published_at
        for metric, value in row.values.items():
            if metric == "reach":  # Unique audiences cannot be added across posts.
                continue
            summary.totals[metric] = summary.totals.get(metric, 0) + value
            summary.coverage[metric] = summary.coverage.get(metric, 0) + 1
        if "views" in row.values and (
            summary.best is None
            or row.values["views"] > summary.best.values.get("views", -1)
        ):
            summary.best = row
    return sorted(
        groups.values(), key=lambda group: (group.name.casefold(), group.network)
    )
