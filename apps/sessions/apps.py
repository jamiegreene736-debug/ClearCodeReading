from django.apps import AppConfig


class InterventionSessionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sessions"
    label = "intervention_sessions"
    verbose_name = "Intervention Sessions"

    def ready(self):
        from . import signals  # noqa: F401
