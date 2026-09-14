"""Administrator invitations and the first-login employee welcome screen."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any, cast

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.views import PasswordResetConfirmView
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.crm.models import Lead
from apps.crm_email.models import Mailbox
from apps.crm_email.security import configuration_errors
from apps.users.invitations import (
    create_invitation,
    deliver_invitation,
    invitation_tokens,
)
from apps.users.models import AuditLog, CustomUser, UserInvitation


class PortalRequest(HttpRequest):
    user: CustomUser


class InviteUserForm(forms.Form):
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name")
    email = forms.EmailField(max_length=254, label="Email address")
    role = forms.ChoiceField(
        label="Account type",
        choices=[
            (CustomUser.Role.CRM_USER, "Backend employee"),
            (CustomUser.Role.TEACHER, "Teacher"),
            (CustomUser.Role.GUARDIAN, "Parent"),
        ],
    )
    phone_number = forms.CharField(max_length=32, required=False, label="Phone number")

    def __init__(self, *args: Any, actor: CustomUser, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not actor.can_manage_crm_users:
            role_field = cast(forms.ChoiceField, self.fields["role"])
            role_field.choices = [
                (CustomUser.Role.TEACHER, "Teacher"),
                (CustomUser.Role.GUARDIAN, "Parent"),
            ]

    def clean_email(self) -> str:
        email = str(self.cleaned_data["email"]).strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


def require_manager(user: CustomUser) -> None:
    if (
        not user.is_active
        or user.is_deleted
        or not (user.can_manage_crm_users or user.role == CustomUser.Role.SCHOOL_ADMIN)
    ):
        raise PermissionDenied("Only administrators can invite users.")


def invitation_feedback(request: PortalRequest, invitation: UserInvitation) -> None:
    if invitation.status == "sent":
        messages.success(
            request,
            f"Invitation sent to {invitation.user.email}. They can choose their password using the email link.",
        )
    else:
        messages.warning(
            request,
            f"Account created for {invitation.user.email}. Invitation {invitation.status}. {invitation.error}",
        )


@login_required
@require_http_methods(["GET", "POST"])
def manage_users(request: PortalRequest) -> HttpResponse:
    require_manager(request.user)
    form = InviteUserForm(
        request.POST if request.method == "POST" else None, actor=request.user
    )
    status = 200
    if request.method == "POST":
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = CustomUser.objects.create_user(
                        username="invited-" + secrets.token_hex(16),
                        password=None,
                        **form.cleaned_data,
                        metadata={
                            "created_by_admin_id": request.user.pk,
                            "created_from_portal": True,
                        },
                    )
                    invitation = create_invitation(user, request.user)
                    Lead.objects.filter(
                        contact_email__iexact=user.email, is_deleted=False
                    ).update(linked_user=user)
                    AuditLog.objects.create(
                        actor=request.user,
                        action="user.invited",
                        entity_type="CustomUser",
                        entity_id=str(user.pk),
                        after={"role": user.role},
                    )
            except IntegrityError:
                form.add_error("email", "A user with this email already exists.")
            else:
                invitation_feedback(
                    request, deliver_invitation(invitation.pk, request.user)
                )
                return redirect("manage_users")
        status = 400
    invitations = UserInvitation.objects.select_related("user").filter(
        user__is_deleted=False
    )
    if not request.user.can_manage_crm_users:
        invitations = invitations.filter(
            created_by=request.user,
            user__role__in=[CustomUser.Role.TEACHER, CustomUser.Role.GUARDIAN],
        )
    return render(
        request,
        "portal/manage_users.html",
        {"form": form, "invitations": invitations.order_by("-created_at")[:100]},
        status=status,
    )


@login_required
@require_POST
def resend_invitation(request: PortalRequest, pk: int) -> HttpResponse:
    require_manager(request.user)
    with transaction.atomic():
        invitation = get_object_or_404(
            UserInvitation.objects.select_for_update(), pk=pk
        )
        if not request.user.can_manage_crm_users and (
            invitation.created_by_id != request.user.pk
            or invitation.user.role
            not in {CustomUser.Role.TEACHER, CustomUser.Role.GUARDIAN}
        ):
            raise PermissionDenied
        if (
            invitation.accepted_at
            or not invitation.user.is_active
            or invitation.user.is_deleted
        ):
            messages.error(request, "This invitation is no longer available.")
            return redirect("manage_users")
        if (
            invitation.attempted_at
            and invitation.attempted_at > timezone.now() - timedelta(minutes=2)
        ):
            messages.warning(
                request,
                "Please wait two minutes between invitations. Check sent mail before trying again.",
            )
            return redirect("manage_users")
        # A new invitation revokes all previously emailed links, including in-browser setup sessions.
        invitation.nonce = secrets.token_hex(32)
        invitation.status = "pending"
        invitation.sent_at = None
        invitation.save()
    invitation_feedback(request, deliver_invitation(invitation.pk, request.user))
    return redirect("manage_users")


class AcceptInvitationView(PasswordResetConfirmView):
    template_name = "registration/accept_invitation.html"
    token_generator = invitation_tokens
    success_url = reverse_lazy("login")

    def get_user(self, uidb64: str) -> CustomUser | None:
        user = cast(CustomUser | None, super().get_user(uidb64))
        if (
            user
            and user.is_active
            and not user.is_deleted
            and UserInvitation.objects.filter(
                user=user, accepted_at__isnull=True
            ).exists()
        ):
            return user
        return None

    def form_valid(self, form: SetPasswordForm[AbstractBaseUser]) -> HttpResponse:
        with transaction.atomic():
            invitation = (
                UserInvitation.objects.select_for_update()
                .select_related("user")
                .get(user=self.user)
            )
            token = self.request.session.get("_password_reset_token", "")
            if (
                invitation.accepted_at
                or not invitation.user.is_active
                or invitation.user.is_deleted
                or not self.token_generator.check_token(invitation.user, token)
            ):
                self.validlink = False
                return self.render_to_response(self.get_context_data())
            response = super().form_valid(form)
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=["accepted_at", "updated_at"])
        messages.success(
            self.request,
            "Your password is set. Log in with your email address to get started.",
        )
        return response


def needs_gmail_welcome(user: CustomUser) -> bool:
    return bool(
        user.last_login is None
        and user.has_crm_access
        and user.role
        not in {
            CustomUser.Role.TEACHER,
            CustomUser.Role.GUARDIAN,
            CustomUser.Role.STUDENT,
        }
        and not Mailbox.objects.filter(
            user=user, status=Mailbox.Status.CONNECTED
        ).exists()
    )


@login_required
@require_http_methods(["GET", "POST"])
def gmail_welcome(request: PortalRequest) -> HttpResponse:
    if not request.user.has_crm_access or request.user.role in {
        CustomUser.Role.TEACHER,
        CustomUser.Role.GUARDIAN,
        CustomUser.Role.STUDENT,
    }:
        return redirect("portal_dashboard")
    if request.method == "POST":
        destination = request.session.pop("onboarding_next", "")
        if destination and url_has_allowed_host_and_scheme(
            destination, {request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(destination)
        return redirect("portal_dashboard")
    return render(
        request,
        "portal/gmail_welcome.html",
        {"configured": settings.CRM_EMAIL_ENABLED and not configuration_errors()},
    )
