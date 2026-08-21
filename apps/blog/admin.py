from django.contrib import admin
from django.utils import timezone

from apps.blog.models import BlogPost


@admin.action(description="Publish selected posts")
def publish_posts(modeladmin, request, queryset):
    now = timezone.now()
    queryset.filter(published_at__isnull=True).update(published_at=now)
    queryset.update(status=BlogPost.Status.PUBLISHED, updated_at=now)


@admin.action(description="Move selected posts back to draft")
def unpublish_posts(modeladmin, request, queryset):
    queryset.update(
        status=BlogPost.Status.DRAFT,
        is_featured=False,
        updated_at=timezone.now(),
    )


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
    list_filter = ("status", "is_featured", "category", "published_at", "updated_at")
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
