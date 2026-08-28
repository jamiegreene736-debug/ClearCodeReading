import json

from django import template

from apps.crm.models import Lead
from apps.crm.surveys import SURVEY_FIELD_LABELS, SURVEY_VALUE_LABELS


register = template.Library()


@register.filter
def crm_field_label(value):
    return SURVEY_FIELD_LABELS.get(
        str(value),
        str(value).replace("_", " ").strip().title(),
    )


@register.filter
def crm_field_value(value):
    labels = {
        **SURVEY_VALUE_LABELS,
        **dict(Lead.RelationshipInterest.choices),
    }
    if isinstance(value, list):
        return ", ".join(labels.get(item, str(item)) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, indent=2, sort_keys=True)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return labels.get(value, value)
