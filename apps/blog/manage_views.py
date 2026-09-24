"""Portal blog CMS: list, write, edit, publish, schedule and remove posts."""

from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.blog.access import EditorRequest, blog_editor_required
from apps.blog.forms import BlogPostForm
from apps.blog.forms import BlogImageUploadForm
from apps.blog.models import BlogImage, BlogPost
from apps.blog.substack import SUBSTACK_PUBLICATION_URL

TABS = (
    ("all", "All posts"),
    ("published", "Published"),
    ("scheduled", "Scheduled"),
    ("drafts", "Drafts"),
)


def _categories() -> list[str]:
    return list(
        BlogPost.objects.exclude(category="")
        .order_by("category")
        .values_list("category", flat=True)
        .distinct()
    )


@blog_editor_required
@require_http_methods(["GET"])
def post_list(request: EditorRequest) -> HttpResponseBase:
    posts = BlogPost.objects.select_related("author")
    tab = request.GET.get("tab", "all")
    if tab not in dict(TABS):
        tab = "all"
    query = request.GET.get("q", "").strip()[:200]
    if query:
        posts = posts.filter(
            Q(title__icontains=query)
            | Q(excerpt__icontains=query)
            | Q(category__icontains=query)
        )
    if tab == "published":
        posts = posts.published()
    elif tab == "scheduled":
        posts = posts.scheduled()
    elif tab == "drafts":
        posts = posts.drafts()
    posts = posts.order_by("-updated_at")
    page = Paginator(posts, 20).get_page(request.GET.get("page"))
    now = timezone.now()
    counts = {
        "all": BlogPost.objects.count(),
        "published": BlogPost.objects.published().count(),
        "scheduled": BlogPost.objects.scheduled().count(),
        "drafts": BlogPost.objects.drafts().count(),
    }
    return render(
        request,
        "blog/manage/list.html",
        {
            "page": page,
            "tab": tab,
            "tabs": TABS,
            "query": query,
            "counts": counts,
            "now": now,
        },
    )


@blog_editor_required
@require_http_methods(["GET", "POST"])
def post_create(request: EditorRequest) -> HttpResponseBase:
    if request.method == "POST":
        form = BlogPostForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            post = form.save(commit=False)
            post.author = request.user
            post.save()
            _feedback(request, post, created=True)
            return redirect("blog_manage:edit", pk=post.pk)
    else:
        form = BlogPostForm(initial={"body_format": BlogPost.BodyFormat.HTML})
    return render(
        request,
        "blog/manage/form.html",
        {"form": form, "post": None, "categories": _categories()},
    )


@blog_editor_required
@require_http_methods(["GET", "POST"])
def post_edit(request: EditorRequest, pk: int) -> HttpResponseBase:
    post = get_object_or_404(BlogPost.objects.select_related("author"), pk=pk)
    if request.method == "POST":
        form = BlogPostForm(request.POST, request.FILES, instance=post, user=request.user)
        if form.is_valid():
            post = form.save()
            _feedback(request, post, created=False)
            return redirect("blog_manage:edit", pk=post.pk)
    else:
        form = BlogPostForm(instance=post)
    return render(
        request,
        "blog/manage/form.html",
        {"form": form, "post": post, "categories": _categories()},
    )


def _feedback(request: EditorRequest, post: BlogPost, *, created: bool) -> None:
    if post.is_live:
        messages.success(
            request,
            f"“{post.title}” is live on the blog." if created
            else f"“{post.title}” was updated and is live on the blog.",
        )
    elif post.is_scheduled:
        when = timezone.localtime(post.published_at).strftime("%b %-d, %Y at %-I:%M %p")
        messages.success(request, f"“{post.title}” is scheduled to publish on {when}.")
    else:
        messages.success(
            request,
            f"Draft “{post.title}” saved. It stays private until you publish it.",
        )


@blog_editor_required
@require_POST
def post_publish(request: EditorRequest, pk: int) -> HttpResponseBase:
    post = get_object_or_404(BlogPost, pk=pk)
    post.status = BlogPost.Status.PUBLISHED
    if post.published_at is None or post.published_at > timezone.now():
        post.published_at = timezone.now()
    post.save()
    messages.success(request, f"“{post.title}” is now live on the blog.")
    return redirect(_next(request, post))


@blog_editor_required
@require_POST
def post_unpublish(request: EditorRequest, pk: int) -> HttpResponseBase:
    post = get_object_or_404(BlogPost, pk=pk)
    post.status = BlogPost.Status.DRAFT
    post.is_featured = False
    post.save()
    messages.success(request, f"“{post.title}” was moved back to drafts and is no longer public.")
    return redirect(_next(request, post))


@blog_editor_required
@require_POST
def post_feature(request: EditorRequest, pk: int) -> HttpResponseBase:
    post = get_object_or_404(BlogPost, pk=pk)
    post.is_featured = not post.is_featured
    post.save(update_fields=["is_featured", "updated_at"])
    verb = "is now featured" if post.is_featured else "is no longer featured"
    messages.success(request, f"“{post.title}” {verb}.")
    return redirect(_next(request, post))


@blog_editor_required
@require_POST
def post_duplicate(request: EditorRequest, pk: int) -> HttpResponseBase:
    original = get_object_or_404(BlogPost, pk=pk)
    copy = BlogPost(
        title=f"{original.title} (copy)",
        excerpt=original.excerpt,
        body=original.body,
        body_format=original.body_format,
        category=original.category,
        cover_data=original.cover_data,
        cover_content_type=original.cover_content_type,
        cover_image=original.cover_image,
        cover_image_alt=original.cover_image_alt,
        author=request.user,
        seo_title=original.seo_title,
        seo_description=original.seo_description,
    )
    copy.save()
    messages.success(request, f"A draft copy of “{original.title}” was created.")
    return redirect("blog_manage:edit", pk=copy.pk)


@blog_editor_required
@require_POST
def post_delete(request: EditorRequest, pk: int) -> HttpResponseBase:
    post = get_object_or_404(BlogPost, pk=pk)
    title = post.title
    post.delete()
    messages.success(request, f"“{title}” was deleted.")
    return redirect("blog_manage:list")


@blog_editor_required
@require_http_methods(["GET"])
def post_preview(request: EditorRequest, pk: int) -> HttpResponseBase:
    """Render the public article template for any post, live or not."""
    post = get_object_or_404(BlogPost.objects.select_related("author"), pk=pk)
    return render(
        request,
        "blog/post_detail.html",
        {
            "post": post,
            "preview": True,
            "substack_publication_url": SUBSTACK_PUBLICATION_URL,
        },
    )


def _next(request: EditorRequest, post: BlogPost) -> str:
    target = request.POST.get("next", "")
    if target.startswith("/portal/blog/"):
        return target
    return "/portal/blog/"


def serve_cover(request, slug: str) -> HttpResponse:
    """Public cover image bytes for a live post; editors can also see drafts."""
    from apps.blog.access import can_manage_blog

    post = get_object_or_404(BlogPost.objects.only("slug", "cover_data", "cover_content_type", "status", "published_at", "updated_at"), slug=slug)
    if not post.cover_data:
        raise Http404("No cover image")
    if not post.is_live and not can_manage_blog(request.user):
        raise Http404("Cover unavailable")
    response = HttpResponse(bytes(post.cover_data), content_type=post.cover_content_type or "image/jpeg")
    response["Cache-Control"] = "public, max-age=3600" if post.is_live else "private, no-store"
    return response


@blog_editor_required
@require_POST
def image_upload(request: EditorRequest) -> HttpResponseBase:
    """Store an article image and return its URL for the rich-text editor."""
    form = BlogImageUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        errors = form.errors.get("image") or form.non_field_errors() or ["Choose an image to upload."]
        return JsonResponse({"error": str(errors[0])}, status=400)
    upload = form.cleaned_data["image"]
    post = None
    post_pk = form.cleaned_data.get("post")
    if post_pk:
        post = BlogPost.objects.filter(pk=post_pk).first()
    image = BlogImage.create_from_bytes(
        upload.read(),
        form.cleaned_data["content_type"],
        post=post,
        uploaded_by=request.user,
        original_name=getattr(upload, "name", "") or "",
    )
    return JsonResponse({"url": image.url, "key": str(image.key), "size": image.size}, status=201)


def serve_image(request, key) -> HttpResponse:
    """Public bytes for an article image."""
    image = get_object_or_404(BlogImage.objects.only("data", "content_type"), key=key)
    response = HttpResponse(bytes(image.data), content_type=image.content_type or "image/jpeg")
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
