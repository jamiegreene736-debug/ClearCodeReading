from collections.abc import Iterable, Mapping
from typing import Any

from django import template


register = template.Library()

GROUP_DEFINITIONS = (
    (
        "learning",
        "Readers & teaching",
        frozenset(
            {
                "assessments",
                "curriculum",
                "decision_support",
                "intervention_sessions",
                "outcomes",
                "progress",
                "readings",
            }
        ),
    ),
    (
        "people",
        "People & access",
        frozenset(
            {
                "accounts",
                "auth",
                "guardian",
                "organizations",
                "schools",
                "tenants",
                "users",
            }
        ),
    ),
    (
        "operations",
        "Scheduling & payments",
        frozenset({"billing", "notifications", "scheduling", "workforce"}),
    ),
    (
        "business",
        "Business & content",
        frozenset({"blog", "core", "crm", "documents", "repositories"}),
    ),
    ("system", "System", frozenset()),
)

APP_GROUPS = {
    app_label: group_key
    for group_key, _group_label, app_labels in GROUP_DEFINITIONS
    for app_label in app_labels
}


@register.simple_tag
def admin_navigation_groups(
    available_apps: Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Group Django's permission-filtered admin apps for horizontal navigation."""
    groups = {
        key: {"key": key, "label": label, "apps": [], "model_count": 0}
        for key, label, _app_labels in GROUP_DEFINITIONS
    }

    for app in available_apps or ():
        group = groups[APP_GROUPS.get(str(app.get("app_label", "")), "system")]
        group["apps"].append(app)
        group["model_count"] += len(app.get("models") or ())

    return [
        groups[key]
        for key, _label, _app_labels in GROUP_DEFINITIONS
        if groups[key]["apps"]
    ]
