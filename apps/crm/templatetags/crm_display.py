import json

from django import template

from apps.crm.models import Lead
from apps.crm.surveys import SURVEY_FIELD_LABELS, SURVEY_VALUE_LABELS


register = template.Library()


SURVEY_DISPLAY_GROUPS = (
    (
        "Situation and goals",
        ("respondent_situation", "engagement_interests"),
    ),
    (
        "Contact details",
        ("name", "email", "home_zip"),
    ),
    (
        "Reading support history",
        ("supports_tried", "annual_reading_spend"),
    ),
    (
        "Family preferences",
        ("commitment_preference", "one_to_one_budget", "small_group_budget"),
    ),
    (
        "Submission details",
        ("survey_placement", "blog_post_title", "email_consent"),
    ),
)


def _field_label(value: object) -> str:
    return SURVEY_FIELD_LABELS.get(
        str(value),
        str(value).replace("_", " ").strip().title(),
    )


def _value_labels() -> dict[str, str]:
    return {
        **SURVEY_VALUE_LABELS,
        **dict(Lead.RelationshipInterest.choices),
    }


def _field_value(value: object) -> object:
    labels = _value_labels()
    if isinstance(value, list):
        return ", ".join(labels.get(item, str(item)) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, indent=2, sort_keys=True)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str) and value.lower() in {"yes", "no"}:
        return value.title()
    return labels.get(value, value)


def _has_answer(value: object) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _survey_answer(key: str, value: object) -> dict[str, object]:
    labels = _value_labels()
    if isinstance(value, list):
        return {
            "label": _field_label(key),
            "kind": "list",
            "is_wide": True,
            "values": [labels.get(item, str(item)) for item in value],
        }
    return {
        "label": _field_label(key),
        "kind": "text",
        "is_wide": key == "respondent_situation",
        "value": _field_value(value),
    }


@register.filter
def crm_field_label(value: object) -> str:
    return _field_label(value)


@register.filter
def crm_field_value(value: object) -> object:
    return _field_value(value)


@register.simple_tag
def crm_survey_sections(submitted_data: object) -> list[dict[str, object]]:
    """Group answered survey fields for a concise, scannable CRM display."""
    if not isinstance(submitted_data, dict):
        return []

    sections = []
    displayed_keys = set()
    for title, keys in SURVEY_DISPLAY_GROUPS:
        answers = []
        for key in keys:
            value = submitted_data.get(key)
            if not _has_answer(value):
                continue
            displayed_keys.add(key)
            answers.append(_survey_answer(key, value))
        if answers:
            sections.append({"title": title, "answers": answers})

    additional_answers = [
        _survey_answer(key, value)
        for key, value in submitted_data.items()
        if key not in displayed_keys
        and key != "blog_post_slug"
        and _has_answer(value)
    ]
    if additional_answers:
        sections.append({"title": "Additional responses", "answers": additional_answers})
    return sections
