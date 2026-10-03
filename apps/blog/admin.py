from django.contrib import admin
from django.utils import timezone

from apps.blog.models import BlogPost


@admin.action(description="Publish selected posts")
def publish_posts(modeladmin, request, queryset):
    now = timezone.now()
    for post in queryset:
        post.status = BlogPost.Status.PUBLISHED
        post.published_at = post.published_at or now
        post.save(update_fields=["status", "published_at", "updated_at"])


@admin.action(description="Move selected posts back to draft")
def unpublish_posts(modeladmin, request, queryset):
    for post in queryset:
        post.status, post.is_featured = BlogPost.Status.DRAFT, False
        post.save(update_fields=["status", "is_featured", "updated_at"])


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "category",
        "author",
        "is_featured",
        "published_at",
        "updated_at",
    )
    list_filter = ("status", "is_featured", "body_format", "category", "published_at", "updated_at")
    search_fields = ("title", "excerpt", "body", "category", "author__email")
    autocomplete_fields = ("author",)
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "published_at"
    ordering = ("-published_at", "-updated_at")
    actions = (publish_posts, unpublish_posts)
    fieldsets = (
        (
            "Article",
            {
                "fields": (
                    "title",
                    "slug",
                    "excerpt",
                    "body_format",
                    "body",
                    "category",
                    "author",
                )
            },
        ),
        (
            "Cover image",
            {"fields": ("cover_image", "cover_image_alt")},
        ),
        (
            "Publication",
            {"fields": ("status", "published_at", "is_featured")},
        ),
        (
            "Search and sharing",
            {
                "classes": ("collapse",),
                "fields": ("seo_title", "seo_description"),
            },
        ),
        (
            "History",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if obj.author_id is None:
            obj.author = request.user
        super().save_model(request, obj, form, change)
