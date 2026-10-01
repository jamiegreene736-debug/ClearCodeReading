from django.contrib import admin

from apps.social.models import SocialAccount, SocialPost, SocialPublication


@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = ("network", "status", "display_name", "username", "connected_at")
    exclude = ("encrypted_token",)


@admin.register(SocialPost)
class SocialPostAdmin(admin.ModelAdmin):
    list_display = ("headline", "status", "scheduled_at", "post_to_facebook", "post_to_instagram")
    exclude = ("image_data",)


@admin.register(SocialPublication)
class SocialPublicationAdmin(admin.ModelAdmin):
    list_display = ("post", "network", "status", "published_at")
