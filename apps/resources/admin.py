from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import admin

from apps.resources.models import Topic

if TYPE_CHECKING:
    TopicModelAdmin = admin.ModelAdmin[Topic]
else:
    TopicModelAdmin = admin.ModelAdmin


@admin.register(Topic)
class TopicAdmin(TopicModelAdmin):
    search_fields = ("name",)
