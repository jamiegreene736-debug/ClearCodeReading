from django.apps import AppConfig


class CrmEmailConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.crm_email"
    verbose_name = "CRM email"

    def ready(self) -> None:
        from apps.crm_email import stage_signals  # noqa: F401
