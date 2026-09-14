import base64
import secrets
from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast
from urllib.parse import quote, urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from apps.crm_email.models import Authorization, Mailbox
from apps.crm_email.security import (
    EmailError,
    EmailRequest,
    decrypt,
    digest,
    encrypt,
    mailbox_lock,
    require_configured,
)
from apps.users.models import AuditLog

SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


class ProviderError(EmailError):
    def __init__(self, status: int = 0, retryable: bool = True) -> None:
        self.status = status
        self.retryable = retryable
        super().__init__(
            "Google email is temporarily unavailable."
            if retryable
            else "Google rejected the email request."
        )


def json_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    # Provider JSON is validated at each use; raw payloads never enter logs or UI errors.
    try:
        response = requests.request(
            method, url, timeout=(5, 30), allow_redirects=False, **kwargs
        )
    except requests.RequestException as exc:
        raise ProviderError() from exc
    if not 200 <= response.status_code < 300:
        retryable = response.status_code in {429, 500, 502, 503, 504}
        if response.status_code == 403:
            try:
                retryable = any(
                    item.get("reason") in {"rateLimitExceeded", "userRateLimitExceeded"}
                    for item in response.json().get("error", {}).get("errors", [])
                )
            except (ValueError, TypeError, AttributeError):
                pass
        raise ProviderError(response.status_code, retryable)
    if response.status_code == 204 or not response.content:
        return {}
    try:
        result = response.json()
    except ValueError as exc:
        raise ProviderError() from exc
    if not isinstance(result, dict):
        raise ProviderError()
    return result


def authorization_url(request: EmailRequest) -> str:
    require_configured()
    if not request.session.session_key:
        request.session.save()
    state, verifier, nonce = (
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(64),
        secrets.token_urlsafe(32),
    )
    Authorization.objects.create(
        state_hash=digest(state),
        user=request.user,
        session_hash=digest(request.session.session_key or ""),
        encrypted_verifier=encrypt(verifier.encode()).decode(),
        nonce=nonce,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    import hashlib

    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
        {
            "client_id": settings.CRM_EMAIL_GOOGLE_CLIENT_ID,
            "redirect_uri": settings.CRM_EMAIL_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent select_account",
            "state": state,
            "nonce": nonce,
            "hd": settings.CRM_EMAIL_DOMAIN,
            "login_hint": request.user.email,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )


def connect(request: EmailRequest) -> Mailbox:
    require_configured()
    with transaction.atomic():
        authorization = (
            Authorization.objects.select_for_update()
            .filter(
                state_hash=digest(request.GET.get("state", "")),
                user=request.user,
                session_hash=digest(request.session.session_key or ""),
                consumed=False,
                expires_at__gt=timezone.now(),
            )
            .first()
        )
        if authorization is None:
            raise EmailError(
                "This Google connection request expired or was already used. Please start again."
            )
        authorization.consumed = True
        authorization.save(update_fields=["consumed"])
    if request.GET.get("error") or not request.GET.get("code"):
        raise EmailError("Google connection was cancelled or denied.")
    token = json_request(
        "POST",
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": settings.CRM_EMAIL_GOOGLE_CLIENT_ID,
            "client_secret": settings.CRM_EMAIL_GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.CRM_EMAIL_REDIRECT_URI,
            "grant_type": "authorization_code",
            "code": request.GET["code"],
            "code_verifier": decrypt(
                authorization.encrypted_verifier.encode()
            ).decode(),
        },
    )
    try:
        claims = cast(Callable[..., dict[str, Any]], id_token.verify_oauth2_token)(
            token.get("id_token", ""),
            GoogleRequest(),
            settings.CRM_EMAIL_GOOGLE_CLIENT_ID,
        )
    except (ValueError, requests.RequestException) as exc:
        raise EmailError(
            "Google account verification failed. Please reconnect."
        ) from exc
    email = str(claims.get("email", "")).lower()
    if (
        claims.get("nonce") != authorization.nonce
        or claims.get("hd") != settings.CRM_EMAIL_DOMAIN
        or claims.get("email_verified") is not True
        or email != request.user.email.lower()
        or email.rsplit("@", 1)[-1] != settings.CRM_EMAIL_DOMAIN
        or not claims.get("sub")
    ):
        raise EmailError(
            "Choose your own ClearCodeReading Google Workspace account matching your CRM email."
        )
    granted = set(str(token.get("scope", "")).split())
    if not set(SCOPES[2:]).issubset(granted) or not token.get("refresh_token"):
        raise EmailError(
            "Approve both sending and reading email so replies can synchronize."
        )
    mailbox, _ = Mailbox.objects.get_or_create(user=request.user)
    with mailbox_lock(mailbox.pk):
        mailbox.refresh_from_db()
        if mailbox.google_subject and mailbox.google_subject != claims["sub"]:
            raise EmailError("Reconnect the originally linked Google account.")
        profile = json_request(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": "Bearer " + token["access_token"]},
        )
        if str(profile.get("emailAddress", "")).lower() != email:
            raise EmailError("Google mailbox does not match your verified account.")
        mailbox.email = email
        mailbox.google_subject = claims["sub"]
        mailbox.encrypted_refresh_token = encrypt(
            token["refresh_token"].encode()
        ).decode()
        mailbox.status = Mailbox.Status.CONNECTED
        mailbox.last_error = ""
        mailbox.failures = 0
        mailbox.next_attempt_at = None
        mailbox.sync_requested_at = timezone.now()
        mailbox.save()
    AuditLog.objects.create(
        actor=request.user,
        action="crm.email.connected",
        entity_type="Mailbox",
        entity_id=str(mailbox.pk),
    )
    return mailbox


class Gmail:
    def __init__(self, mailbox: Mailbox) -> None:
        self.mailbox = mailbox
        self.access_token = ""

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        require_configured()
        if not self.access_token:
            try:
                token = json_request(
                    "POST",
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": settings.CRM_EMAIL_GOOGLE_CLIENT_ID,
                        "client_secret": settings.CRM_EMAIL_GOOGLE_CLIENT_SECRET,
                        "grant_type": "refresh_token",
                        "refresh_token": decrypt(
                            self.mailbox.encrypted_refresh_token.encode()
                        ).decode(),
                    },
                )
            except ProviderError as exc:
                if exc.status in {400, 401}:
                    Mailbox.objects.filter(pk=self.mailbox.pk).update(
                        status=Mailbox.Status.RECONNECT,
                        last_error="Google access expired or was revoked. Reconnect your mailbox.",
                    )
                    raise EmailError("Reconnect your Google mailbox.") from exc
                raise
            self.access_token = str(token.get("access_token", ""))
            if not self.access_token:
                raise ProviderError()
        try:
            return json_request(
                method,
                "https://gmail.googleapis.com/gmail/v1/users/me/" + path,
                headers={"Authorization": "Bearer " + self.access_token},
                **kwargs,
            )
        except ProviderError as exc:
            if exc.status == 401:
                Mailbox.objects.filter(pk=self.mailbox.pk).update(
                    status=Mailbox.Status.RECONNECT,
                    last_error="Google access expired or was revoked. Reconnect your mailbox.",
                )
            raise

    def thread(self, thread_id: str) -> dict[str, Any]:
        return self.request(
            "GET", "threads/" + quote(thread_id, safe=""), params={"format": "full"}
        )


def revoke(mailbox: Mailbox) -> bool:
    if not mailbox.encrypted_refresh_token:
        return True
    try:
        response = requests.post(
            "https://oauth2.googleapis.com/revoke",
            data={"token": decrypt(mailbox.encrypted_refresh_token.encode()).decode()},
            timeout=(5, 15),
            allow_redirects=False,
        )
        return response.status_code in {200, 400}
    except (requests.RequestException, EmailError):
        return False
