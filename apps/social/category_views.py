"""Manage categories and classify existing posts without republishing them."""

from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.social.access import SocialRequest, social_editor_required
from apps.social.categories import apply_category, category_choices, save_category
from apps.social.exceptions import SocialError
from apps.social.models import SocialCategory, SocialPost


@social_editor_required
@require_http_methods(["GET", "POST"])
def categories(request: SocialRequest) -> HttpResponse:
    if request.method == "POST":
        try:
            with transaction.atomic():
                action = request.POST.get("action")
                if action == "bulk":
                    ids = request.POST.getlist("posts")
                    if (
                        not ids
                        or len(ids) > 100
                        or any(not pk.isdecimal() or len(pk) > 18 for pk in ids)
                    ):
                        raise SocialError("Select up to 100 posts.")
                    selected_posts = list(
                        SocialPost.objects.select_for_update()
                        .filter(pk__in=ids)
                        .order_by("pk")
                    )
                    candidate = SocialPost()
                    apply_category(candidate, request.POST)
                    SocialPost.objects.filter(
                        pk__in=[post.pk for post in selected_posts]
                    ).update(category=candidate.category, updated_at=timezone.now())
                    messages.success(
                        request,
                        f"Category updated for {len(selected_posts)} posts. Published content is unchanged.",
                    )
                elif action in {"save", "archive", "restore"}:
                    category_id = request.POST.get("category_id", "")
                    if category_id and (
                        not category_id.isdecimal() or len(category_id) > 18
                    ):
                        raise SocialError("Choose a valid category.")
                    category = (
                        get_object_or_404(
                            SocialCategory.objects.select_for_update(), pk=category_id
                        )
                        if category_id
                        else SocialCategory()
                    )
                    if action == "save":
                        save_category(
                            category,
                            request.POST.get("name", ""),
                            request.POST.get("color", ""),
                        )
                    elif category.pk:
                        category.archived = action == "archive"
                        category.save(update_fields=["archived", "updated_at"])
                    messages.success(request, "Category saved.")
                else:
                    raise SocialError("Choose a category action.")
            return redirect("social:categories")
        except SocialError as exc:
            messages.error(request, str(exc))
    posts = (
        SocialPost.objects.select_related("category")
        .defer("image_data")
        .order_by("-created_at")
    )
    query = request.GET.get("q", "")[:200]
    if query:
        posts = posts.filter(facebook_caption__icontains=query)
    if request.GET.get("uncategorized"):
        posts = posts.filter(category__isnull=True)
    return render(
        request,
        "social/categories.html",
        {
            "categories": SocialCategory.objects.all(),
            "category_choices": category_choices(),
            "page": Paginator(posts, 50).get_page(request.GET.get("page")),
            "query": query,
        },
    )
