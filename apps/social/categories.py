"""Internal categorization, independent of public post content."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.http import QueryDict

from apps.social.exceptions import SocialError
from apps.social.models import SocialCategory, SocialPost


def category_choices(post: SocialPost | None = None) -> QuerySet[SocialCategory]:
    return SocialCategory.objects.filter(
        Q(archived=False) | Q(pk=post.category_id if post else None)
    )


def save_category(category: SocialCategory, name: str, color: str) -> SocialCategory:
    category.name, category.color = name.strip(), color.upper()
    try:
        category.full_clean()
        with transaction.atomic():
            category.save()
    except (ValidationError, IntegrityError) as exc:
        raise SocialError(
            "Use a unique category name (up to 80 characters) and a valid color."
        ) from exc
    return category


def apply_category(post: SocialPost, data: QueryDict) -> None:
    """Called within the caller's transaction, after validating public content."""
    if "category" not in data:
        return
    value = data.get("category", "")
    if not value:
        post.category = None
        return
    if value == "new":
        post.category = save_category(
            SocialCategory(),
            data.get("category_name", ""),
            data.get("category_color", "#197B80"),
        )
        return
    if not value.isdecimal() or len(value) > 18:
        raise SocialError("Choose a valid category.")
    category = SocialCategory.objects.select_for_update().filter(pk=int(value)).first()
    if category is None or (category.archived and post.category_id != category.pk):
        raise SocialError(
            "This category is archived or unavailable. Choose an active category."
        )
    if data.get("change_category_color") == "on":
        save_category(category, category.name, data.get("category_color", ""))
    post.category = category
