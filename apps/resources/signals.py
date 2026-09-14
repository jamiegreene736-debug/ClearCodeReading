from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in, dispatch_uid="resources_staff_login_source")
def mark_login_source(sender, request, user, **kwargs) -> None:
    if request is not None:
        # The portal also offers a public demo-admin login. It is not proof of staff identity.
        request.session["resources_staff_login"] = not request.path.startswith(
            "/demo-login/"
        )
