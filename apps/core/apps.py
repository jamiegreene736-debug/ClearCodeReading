from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"

    def ready(self) -> None:
        from apps.core.bot_protection import install_test_client_human_check, running_under_test_runner

        if running_under_test_runner():
            install_test_client_human_check()
