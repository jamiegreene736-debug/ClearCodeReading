from __future__ import annotations

from django.apps import AppConfig


class ResourcesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.resources"
    verbose_name = "Website resources"

    def ready(self) -> None:
        from apps.resources import signals  # noqa: F401
