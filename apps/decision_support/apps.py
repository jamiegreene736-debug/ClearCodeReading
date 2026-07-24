from django.apps import AppConfig


class DecisionSupportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.decision_support"

    def ready(self):
        from . import signals  # noqa: F401
