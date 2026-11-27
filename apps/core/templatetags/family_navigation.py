"""Guardian child links in the shared portal navigation."""

from django import template
from django.db.models import Exists, OuterRef

from apps.users.family import editable_family_relationships
from apps.users.models import SignedReaderForms

register = template.Library()


@register.simple_tag
def guardian_profile_links(guardian):
    return editable_family_relationships(guardian).annotate(
        has_signed_forms=Exists(
            SignedReaderForms.objects.filter(relationship_id=OuterRef("pk"))
        )
    ).order_by("child__first_name", "child__last_name", "child_id")
