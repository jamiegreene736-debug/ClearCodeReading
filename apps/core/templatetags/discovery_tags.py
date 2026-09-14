import json

from django import template
from django.utils.safestring import mark_safe

from apps.core.discovery import FAQ_ENTRIES, faq_page_graph, orlando_page_graph

register = template.Library()


@register.simple_tag
def discovery_faqs():
    return FAQ_ENTRIES


@register.simple_tag
def faq_json_ld():
    return mark_safe(json.dumps(faq_page_graph(), ensure_ascii=False))


@register.simple_tag
def orlando_json_ld():
    return mark_safe(json.dumps([orlando_page_graph(), faq_page_graph()], ensure_ascii=False))
