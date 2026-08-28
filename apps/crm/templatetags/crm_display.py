from django import template


register = template.Library()


@register.filter
def crm_field_label(value):
    return str(value).replace("_", " ").strip().title()
