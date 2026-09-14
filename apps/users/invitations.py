"""Private, single-use account setup and explicit delivery receipts."""

import base64
import secrets
from email.message import EmailMessage
from smtplib import SMTPException
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.crm_email.google import Gmail, ProviderError
from apps.crm_email.security import EmailError, mailbox_lock, require_configured
from apps.crm_email.services import active_mailbox
from apps.users.models import CustomUser, UserInvitation


class InvitationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "clearcode.user_invitation"

    def _make_hash_value(self, user: CustomUser, timestamp: int) -> str:
        return f"{super()._make_hash_value(user, timestamp)}{user.invitation.nonce}"


invitation_tokens = InvitationTokenGenerator()


def create_invitation(user: CustomUser, actor: CustomUser) -> UserInvitation:
    return UserInvitation.objects.create(
        user=user, created_by=actor, nonce=secrets.token_hex(32)
    )


def deliver_invitation(invitation_id: int, actor: CustomUser) -> UserInvitation:
    # Claim delivery before external I/O. A second click cannot send in parallel.
    with transaction.atomic():
        invitation = (
            UserInvitation.objects.select_for_update()
            .select_related("user")
            .get(pk=invitation_id)
        )
        if (
            invitation.accepted_at
            or not invitation.user.is_active
            or invitation.user.is_deleted
        ):
            return invitation
        if invitation.status != "pending":
            return invitation
        invitation.status = "sending"
        invitation.attempted_at = timezone.now()
        invitation.error = ""
        invitation.save()
    try:
        _send(invitation, actor)
    except ProviderError as exc:
        invitation.status = (
            "uncertain" if exc.status == 0 or exc.status >= 500 else "failed"
        )
        invitation.error = (
            "Delivery could not be confirmed. Check your Gmail Sent folder before sending another invitation."
            if invitation.status == "uncertain"
            else "Google rejected the invitation. Check your email connection and try again."
        )
    except (SMTPException, OSError):
        invitation.status = "uncertain"
        invitation.error = "Delivery could not be confirmed. Check your sent mail before sending another invitation."
    except (PermissionDenied, ImproperlyConfigured):
        invitation.status = "failed"
        invitation.error = "The sender cannot send invitations. Ask an administrator with a connected work mailbox to resend."
    except EmailError as exc:
        invitation.status = "failed"
        invitation.error = str(exc)
    else:
        invitation.status = "sent"
        invitation.sent_at = timezone.now()
    invitation.save(update_fields=["status", "error", "sent_at", "updated_at"])
    return invitation


def _send(invitation: UserInvitation, actor: CustomUser) -> None:
    base = settings.PUBLIC_APP_URL.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise EmailError(
            "Set the public HTTPS website address before sending invitations."
        )
    user = invitation.user
    path = reverse(
        "user_invitation_accept",
        kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
            "token": invitation_tokens.make_token(user),
        },
    )
    role = (
        "Backend employee"
        if user.role == CustomUser.Role.CRM_USER
        else user.get_role_display()
    )
    subject = "Your ClearCode Reading account is ready"
    body = (
        f"Hello {user.first_name or user.email},\n\n"
        f"Your ClearCode Reading account has been created.\n"
        f"Email / login: {user.email}\nAccount type: {role}\n\n"
        f"Choose your password and finish setting up your account:\n{base}{path}\n\n"
        f"This private link expires in {settings.PASSWORD_RESET_TIMEOUT // 3600} hours and can be used once. "
        "If it expires, ask your administrator to resend your invitation.\n\n"
        f"After setup, log in here: {base}{reverse('login')}\n"
    )
    if user.role == CustomUser.Role.CRM_USER:
        body += (
            "\nOn your first login, we will help you connect your work Gmail account.\n"
        )
    if settings.CRM_EMAIL_ENABLED:
        require_configured()
        mailbox = active_mailbox(actor)
        message = EmailMessage()
        message["From"] = mailbox.email
        message["To"] = user.email
        message["Subject"] = subject
        message["Message-ID"] = (
            f"<user-invitation-{invitation.pk}-{secrets.token_hex(8)}@{settings.CRM_EMAIL_DOMAIN}>"
        )
        message.set_content(body)
        # Do not create a CRM Message: it would expose the setup link to colleagues.
        with mailbox_lock(mailbox.pk):
            result = Gmail(mailbox).request(
                "POST",
                "messages/send",
                json={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()},
            )
        if not result.get("id"):
            raise ProviderError()
    else:
        if settings.EMAIL_BACKEND in {
            "django.core.mail.backends.locmem.EmailBackend",
            "django.core.mail.backends.console.EmailBackend",
            "django.core.mail.backends.dummy.EmailBackend",
            "django.core.mail.backends.filebased.EmailBackend",
        } and not getattr(settings, "USER_INVITATIONS_ALLOW_TEST_EMAIL", False):
            raise EmailError(
                "Email delivery is not configured. Connect your work Gmail or configure outgoing email, then resend."
            )
        if (
            send_mail(
                subject,
                body,
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            != 1
        ):
            raise EmailError(
                "The email provider did not accept the invitation. Please try again."
            )
