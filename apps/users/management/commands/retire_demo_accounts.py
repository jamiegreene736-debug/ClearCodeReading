from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q

from apps.users.models import AuditLog, CustomUser
from apps.users.portal_views import DEMO_LOGINS


class Command(BaseCommand):
    help = "Disable public demo accounts without deleting their records."

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.ENABLE_DEMO_ACCESS:
            raise CommandError("Disable demo access before retiring demo accounts.")
        if getattr(connection, "schema_name", "") != "public":
            raise CommandError("Run demo retirement on the public schema.")
        retired: list[str] = []
        with transaction.atomic():
            users = CustomUser.objects.select_for_update().filter(
                Q(metadata__demo=True) | Q(email__in=DEMO_LOGINS.values())
            )
            for user in users:
                if not user.is_active and not user.has_usable_password():
                    continue
                user.is_active = False
                user.set_unusable_password()
                user.save(update_fields=["is_active", "password", "updated_at"])
                retired.append(str(user.pk))
            if retired:
                AuditLog.objects.create(
                    action="security.demo_accounts.retired",
                    entity_type="CustomUser",
                    entity_id="public-demo",
                    after={"retired_user_ids": retired},
                    metadata={"source": "production_predeploy"},
                )
        self.stdout.write(
            f"Retired {len(retired)} public demo accounts; records preserved."
        )
