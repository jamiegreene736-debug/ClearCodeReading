"""Read-only Meta metrics, with explicit missing-data and permission handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from urllib.parse import quote

import requests

from apps.social.crypto import decrypt_text
from apps.social.meta import _version
from apps.social.models import SocialAccount, SocialPublication

METRICS = {
    "views": "Views",
    "reach": "Reach / viewers",
    "likes": "Likes / reactions",
    "comments": "Comments",
    "shares": "Shares",
    "saves": "Saves",
    "clicks": "Link clicks",
}


class InsightsError(Exception):
    def __init__(self, message: str, kind: str = "temporary") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class MetricResult:
    values: dict[str, int]
    unavailable: dict[str, str]
    warning: str = ""
    retry_kind: str = ""


def request_metrics(url: str, token: str, params: dict[str, str]) -> dict[str, object]:
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=(10, 20),
        )
    except requests.RequestException as exc:
        raise InsightsError(
            "Meta could not be reached. We will retry automatically."
        ) from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise InsightsError(
            "Meta returned an unreadable response. We will retry automatically."
        ) from exc
    if not isinstance(body, dict):
        raise InsightsError(
            "Meta returned an unreadable response. We will retry automatically."
        )
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if response.status_code in {401, 403} or code in {10, 102, 190, 200}:
        raise InsightsError(
            "Analytics permission is missing or expired. Reconnect with analytics access in Connections.",
            "permission",
        )
    if response.status_code == 429 or code in {4, 17, 32, 613}:
        raise InsightsError(
            "Meta is limiting requests. Updates will resume automatically.",
            "rate_limit",
        )
    if response.status_code >= 500:
        raise InsightsError(
            "Meta is temporarily unavailable. We will retry automatically."
        )
    if response.status_code >= 400 or error:
        raise InsightsError(
            "Not available for this post or API version.", "unsupported"
        )
    return cast(dict[str, object], body)


def count(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def nested_count(body: dict[str, object], field: str) -> int | None:
    value = body.get(field)
    if isinstance(value, dict):
        summary = value.get("summary")
        return (
            count(summary.get("total_count"))
            if isinstance(summary, dict)
            else count(value.get("count"))
        )
    return count(value)


def insight_value(body: dict[str, object], metric: str) -> int | None:
    data = body.get("data")
    if not isinstance(data, list):
        return None
    for row in data:
        if not isinstance(row, dict) or row.get("name") != metric:
            continue
        total = row.get("total_value")
        if isinstance(total, dict):
            return count(total.get("value"))
        values = row.get("values")
        if isinstance(values, list) and values and isinstance(values[-1], dict):
            return count(values[-1].get("value"))
    return None


def fetch_metrics(
    publication: SocialPublication, account: SocialAccount
) -> MetricResult:
    token = decrypt_text(account.encrypted_token)
    if not token:
        raise InsightsError(
            "Reconnect with analytics access in Connections.", "permission"
        )
    instagram = publication.network == "instagram"
    host = (
        "graph.instagram.com"
        if instagram and not account.via_facebook
        else "graph.facebook.com"
    )
    url = f"https://{host}/{_version()}/{quote(publication.external_id, safe='')}"
    fields = (
        {"likes": "like_count", "comments": "comments_count"}
        if instagram
        else {
            "likes": "reactions.limit(0).summary(true)",
            "comments": "comments.limit(0).summary(true)",
            "shares": "shares",
        }
    )
    insight_names = (
        {"views": "views", "reach": "reach", "shares": "shares", "saves": "saved"}
        if instagram
        else {
            "views": "post_media_view",
            "reach": "post_total_media_view_unique",
            "clicks": "post_clicks_by_type",
        }
    )
    result = MetricResult(
        {}, {key: "Not provided for this platform." for key in METRICS}
    )
    for key, field in fields.items():
        try:
            body = request_metrics(url, token, {"fields": field})
            value = nested_count(body, field.split(".")[0])
        except InsightsError as exc:
            if exc.kind != "unsupported":
                result.warning, result.retry_kind = str(exc), exc.kind
                result.unavailable.update(
                    {key: str(exc) for key in METRICS if key not in result.values}
                )
                return result
            result.unavailable[key] = str(exc)
            continue
        if value is not None:
            result.values[key] = value
            result.unavailable.pop(key, None)
        else:
            result.unavailable[key] = "Meta has not returned this metric."
    for key, metric in insight_names.items():
        try:
            body = request_metrics(url + "/insights", token, {"metric": metric})
            value = insight_value(body, metric)
            # Total post clicks include unrelated interactions; retain only link clicks.
            if key == "clicks":
                data = body.get("data")
                if isinstance(data, list):
                    for row in data:
                        if isinstance(row, dict) and row.get("name") == metric:
                            values = row.get("values")
                            if (
                                isinstance(values, list)
                                and values
                                and isinstance(values[-1], dict)
                            ):
                                breakdown = values[-1].get("value")
                                value = (
                                    count(breakdown.get("link clicks"))
                                    if isinstance(breakdown, dict)
                                    else None
                                )
        except InsightsError as exc:
            if exc.kind != "unsupported":
                result.warning, result.retry_kind = str(exc), exc.kind
                result.unavailable.update(
                    {key: str(exc) for key in METRICS if key not in result.values}
                )
                return result
            result.unavailable[key] = str(exc)
            continue
        if value is not None:
            result.values[key] = value
            result.unavailable.pop(key, None)
        else:
            result.unavailable[key] = "Meta has not returned this metric."
    return result
