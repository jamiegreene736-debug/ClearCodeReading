from __future__ import annotations

from typing import Any

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.http import HttpRequest

from apps.users.models import CustomUser


@receiver(user_logged_in, dispatch_uid="resources_staff_login_source")
def mark_login_source(
    sender: type[CustomUser] | None,
    request: HttpRequest | None,
    user: CustomUser,
    **kwargs: Any,
) -> None:
    if request is not None:
        # The portal also offers a public demo-admin login. It is not proof of staff identity.
        request.session["resources_staff_login"] = not request.path.startswith(
            "/demo-login/"
        )
