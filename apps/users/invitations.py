"""Private, single-use account setup and explicit delivery receipts."""

import base64
import secrets
from email.message import EmailMessage
from email.utils import formataddr
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

from apps.crm_email.automated import (
    bethany_signature_html,
    copy_for,
    fill,
    fill_html,
)
from apps.crm_email.google import Gmail, ProviderError
from apps.crm_email.models import BETHANY_EMAIL, BETHANY_SIGNATURE, Mailbox
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


def deliver_invitation(invitation_id: int) -> UserInvitation:
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
        _send(invitation)
    except ProviderError as exc:
        invitation.status = (
            "uncertain" if exc.status == 0 or exc.status >= 500 else "failed"
        )
        invitation.error = (
            "Delivery could not be confirmed. Check Bethany’s Gmail Sent folder before sending another invitation."
            if invitation.status == "uncertain"
            else "Google rejected the invitation. Check Bethany’s mailbox connection and try again."
        )
    except (SMTPException, OSError):
        invitation.status = "uncertain"
        invitation.error = "Delivery could not be confirmed. Check Bethany’s sent mail before sending another invitation."
    except (PermissionDenied, ImproperlyConfigured):
        invitation.status = "failed"
        invitation.error = "Bethany’s mailbox cannot send invitations. Check her account access before resending."
    except EmailError as exc:
        invitation.status = "failed"
        invitation.error = str(exc)
    else:
        invitation.status = "sent"
        invitation.sent_at = timezone.now()
    invitation.save(update_fields=["status", "error", "sent_at", "updated_at"])
    return invitation


def _send(invitation: UserInvitation) -> None:
    base = (settings.ACCOUNT_INVITATION_BASE_URL or settings.PUBLIC_APP_URL).rstrip("/")
    try:
        parsed = urlsplit(base)
    except ValueError as exc:
        raise EmailError("The account invitation domain is invalid.") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.endswith(".up.railway.app")
        or parsed.username is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise EmailError(
            "Set the branded HTTPS account invitation domain before sending invitations."
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
    # Wording is editable from CRM email settings (key "account_invitation").
    copy = copy_for("account_invitation")
    values = {
        "first_name": " ".join((user.first_name or user.email).split()),
        "email": user.email,
        "role": role,
        "setup_link": base + path,
        "expires_hours": str(settings.PASSWORD_RESET_TIMEOUT // 3600),
        "login_link": base + reverse("login"),
        "crm_note": (
            "\nOn your first login, we will help you connect your work Gmail account.\n"
            if user.role == CustomUser.Role.CRM_USER
            else ""
        ),
    }
    subject = fill(copy["subject"], values)
    body = fill(copy.text("body"), values).rstrip() + "\n\n" + BETHANY_SIGNATURE
    body_html = (
        fill_html(copy.html("body"), values)
        + f"<p>{bethany_signature_html(public_url=base)}</p>"
    )
    sender = formataddr(("Bethany Fleming", BETHANY_EMAIL))
    if settings.CRM_EMAIL_ENABLED or not getattr(
        settings, "USER_INVITATIONS_ALLOW_TEST_EMAIL", False
    ):
        require_configured()
        mailbox = (
            Mailbox.objects.select_related("user")
            .filter(
                email__iexact=BETHANY_EMAIL,
                user__email__iexact=BETHANY_EMAIL,
                status=Mailbox.Status.CONNECTED,
            )
            .first()
        )
        if mailbox is None:
            raise EmailError(
                "Connect Bethany Fleming’s ClearCode Gmail mailbox before resending."
            )
        message = EmailMessage()
        message["From"] = sender
        message["To"] = user.email
        message["Subject"] = subject
        message["Message-ID"] = (
            f"<user-invitation-{invitation.pk}-{secrets.token_hex(8)}@{settings.CRM_EMAIL_DOMAIN}>"
        )
        message.set_content(body)
        message.add_alternative(body_html, subtype="html")
        # Do not create a CRM Message: it would expose the setup link to colleagues.
        with mailbox_lock(mailbox.pk):
            connected = active_mailbox(mailbox.user)
            result = Gmail(connected).request(
                "POST",
                "messages/send",
                json={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()},
            )
        if not result.get("id"):
            raise ProviderError()
    else:
        if (
            send_mail(
                subject,
                body,
                sender,
                [user.email],
                fail_silently=False,
                html_message=body_html,
            )
            != 1
        ):
            raise EmailError(
                "The email provider did not accept the invitation. Please try again."
            )
