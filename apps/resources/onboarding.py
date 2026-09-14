"""One-use publisher setup links, issued only by an authenticated server operator."""

import hashlib
from uuid import uuid4

from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Permission
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.resources.access import DEMO_IDENTITIES, public_site
from apps.resources.models import StaffSetupToken
from apps.users.models import CustomUser


class StaffSetupForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = CustomUser
        fields = ("email", "first_name", "last_name")

    def clean_email(self) -> str:
        from django.core.exceptions import ValidationError

        email = self.cleaned_data["email"].strip().lower()
        if email in DEMO_IDENTITIES:
            raise ValidationError(
                "Use your own email address, not a public demo identity."
            )
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise ValidationError(
                "This email already has an account. Use another email or sign in with that account."
            )
        return email


@public_site
@require_http_methods(["GET", "POST"])
def setup_staff(request: HttpRequest, token: str) -> HttpResponse:
    digest = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        invitation = get_object_or_404(
            StaffSetupToken.objects.select_for_update(),
            digest=digest,
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        form = StaffSetupForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            user = form.save(commit=False)
            user.username = f"resource-{uuid4().hex[:20]}"
            user.save()
            user.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label="resources", codename="publish_resource"
                )
            )
            invitation.used_at = timezone.now()
            invitation.save(update_fields=["used_at"])
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("resources:manager")
    response = render(request, "resources/setup.html", {"form": form})
    response["Referrer-Policy"] = "no-referrer"
    return response
