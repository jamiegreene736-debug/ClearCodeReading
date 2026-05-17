from django.contrib import admin

from .models import Domain


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "tenant", "is_primary")
    list_filter = ("is_primary", "tenant")
    search_fields = ("domain", "tenant__name", "tenant__schema_name")
    autocomplete_fields = ("tenant",)
