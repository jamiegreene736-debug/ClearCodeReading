import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

import nh3
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import HttpRequest
from django.utils.html import strip_tags

from apps.users.models import CustomUser


class EmailRequest(HttpRequest):
    user: CustomUser


class EmailError(Exception):
    """Safe, user-facing error; never include provider payloads or credentials."""


def configuration_errors() -> list[str]:
    required = [
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "REDIRECT_URI",
        "ENCRYPTION_KEYS",
        "PUBSUB_TOPIC",
        "PUBSUB_AUDIENCE",
        "PUBSUB_EMAIL",
    ]
    missing = [name for name in required if not getattr(settings, "CRM_EMAIL_" + name)]
    if settings.ENABLE_DEMO_ACCESS:
        missing.append("Public demo access must be disabled before connecting email")
    if settings.SECRET_KEY == "dev-only-change-me" or len(settings.SECRET_KEY) < 32:
        missing.append("DJANGO_SECRET_KEY must be a strong production secret")
    if (
        settings.CRM_EMAIL_REDIRECT_URI
        and not settings.CRM_EMAIL_REDIRECT_URI.startswith("https://")
    ):
        missing.append("REDIRECT_URI must use HTTPS")
    if (
        settings.CRM_EMAIL_PUBSUB_AUDIENCE
        and not settings.CRM_EMAIL_PUBSUB_AUDIENCE.startswith("https://")
    ):
        missing.append("PUBSUB_AUDIENCE must use HTTPS")
    if settings.CRM_EMAIL_ENCRYPTION_KEYS:
        try:
            cipher()
        except EmailError:
            missing.append("ENCRYPTION_KEYS invalid")
    return missing


def require_configured() -> None:
    if not settings.CRM_EMAIL_ENABLED or configuration_errors():
        raise EmailError(
            "Google email setup is not complete. Contact your administrator."
        )


def cipher() -> MultiFernet:
    try:
        if not settings.CRM_EMAIL_ENCRYPTION_KEYS:
            raise ValueError("missing key")
        return MultiFernet(
            [Fernet(key.encode()) for key in settings.CRM_EMAIL_ENCRYPTION_KEYS]
        )
    except (ValueError, TypeError) as exc:
        raise EmailError("Email encryption is not configured correctly.") from exc


def encrypt(value: bytes) -> bytes:
    return cipher().encrypt(value)


def decrypt(value: bytes) -> bytes:
    try:
        return cipher().decrypt(value)
    except InvalidToken as exc:
        raise EmailError(
            "Email data cannot be decrypted. Contact your administrator."
        ) from exc


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def clean_html(value: str) -> str:
    return nh3.clean(
        value,
        tags={
            "p",
            "div",
            "br",
            "strong",
            "b",
            "em",
            "i",
            "u",
            "ul",
            "ol",
            "li",
            "blockquote",
            "a",
        },
        attributes={"a": {"href", "title"}},
        url_schemes={"https", "http", "mailto"},
        url_relative="deny",
        clean_content_tags={"script", "style", "iframe", "object", "svg", "math"},
    )


def plain_text(value: str) -> str:
    import html

    return html.unescape(
        strip_tags(
            value.replace("<br>", "\n")
            .replace("</p>", "</p>\n")
            .replace("</div>", "</div>\n")
        )
    )


def require_crm(user: CustomUser) -> None:
    if (
        not user.is_authenticated
        or not user.is_active
        or user.is_deleted
        or not user.has_crm_access
        or getattr(connection, "schema_name", "") != "public"
    ):
        raise PermissionDenied


@contextmanager
def mailbox_lock(mailbox_id: int, *, wait: bool = False) -> Iterator[None]:
    # Session advisory locks also coordinate disconnect/reconnect with in-flight Google calls.
    with connection.cursor() as cursor:
        if wait:
            cursor.execute("SELECT pg_advisory_lock(%s, %s)", [73491, mailbox_id])
        else:
            cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [73491, mailbox_id])
            if not cursor.fetchone()[0]:
                raise EmailError(
                    "Your mailbox is processing another request. Please try again shortly."
                )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [73491, mailbox_id])
