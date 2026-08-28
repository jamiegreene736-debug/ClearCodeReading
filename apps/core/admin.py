from django.contrib import admin

from apps.core.models import RecruitingInterest


@admin.register(RecruitingInterest)
class RecruitingInterestAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "career_path", "role_interest", "status", "created_at")
    list_filter = ("career_path", "status", "created_at")
    search_fields = ("name", "email", "role_interest", "notes")
    readonly_fields = ("created_at", "updated_at", "source_path")
