from django import template

from apps.crm.models import Lead


register = template.Library()


@register.filter
def crm_field_label(value):
    return str(value).replace("_", " ").strip().title()


@register.filter
def crm_field_value(value):
    if isinstance(value, list):
        labels = dict(Lead.RelationshipInterest.choices)
        return ", ".join(labels.get(item, str(item)) for item in value)
    return value
