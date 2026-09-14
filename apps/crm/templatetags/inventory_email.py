from django import template

from apps.crm.inventory_email import split_signoff

register = template.Library()


@register.filter
def before_action(body: str) -> str:
    return split_signoff(body)[0]


@register.filter
def after_action(body: str) -> str:
    return split_signoff(body)[1]
