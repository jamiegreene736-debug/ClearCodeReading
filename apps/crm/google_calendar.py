"""Google calendar consent and minimal free/busy reads for consultation hosts."""

import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from cryptography.fernet import InvalidToken
from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from apps.crm.calendar_models import CalendarAuthorization, HostCalendar
from apps.crm.calendars import CalendarError, cipher
from apps.users.models import CustomUser

SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"
STATE_PREFIX = "calendar."


def configured() -> bool:
    return bool(
        settings.CRM_CALENDAR_GOOGLE_CLIENT_ID
        and settings.CRM_CALENDAR_GOOGLE_CLIENT_SECRET
        and settings.CRM_CALENDAR_REDIRECT_URI.startswith("https://")
        and not settings.ENABLE_DEMO_ACCESS
        and len(settings.SECRET_KEY) >= 32
    )


def require_configured() -> None:
    if not configured():
        raise CalendarError(
            "Google Calendar setup is not complete. Contact your administrator."
        )


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def provider_request(url: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = requests.post(url, timeout=(5, 15), allow_redirects=False, **kwargs)
        if response.status_code != 200:
            raise CalendarError(
                "Google Calendar could not be checked. Try again or reconnect your account."
            )
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError
        return data
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise CalendarError(
            "Google Calendar is temporarily unavailable. Please try again."
        ) from exc


def authorization_url(request: HttpRequest) -> str:
    require_configured()
    if not request.session.session_key:
        request.session.save()
    state = STATE_PREFIX + secrets.token_urlsafe(32)
    verifier, nonce = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
    CalendarAuthorization.objects.create(
        state_hash=digest(state),
        user=request.user,
        session_hash=digest(request.session.session_key or ""),
        encrypted_verifier=cipher().encrypt(verifier.encode()).decode(),
        nonce=nonce,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
        {
            "client_id": settings.CRM_CALENDAR_GOOGLE_CLIENT_ID,
            "redirect_uri": settings.CRM_CALENDAR_REDIRECT_URI,
            "response_type": "code",
            "scope": f"openid email {SCOPE}",
            "access_type": "offline",
            "prompt": "consent select_account",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )


def connect(request: HttpRequest) -> None:
    require_configured()
    with transaction.atomic():
        authorization = (
            CalendarAuthorization.objects.select_for_update()
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
            raise CalendarError(
                "This Google connection request expired or was already used. Please start again."
            )
        authorization.consumed = True
        authorization.save(update_fields=["consumed"])
    if request.GET.get("error") or not request.GET.get("code"):
        raise CalendarError(
            "Google Calendar connection was cancelled. Your existing calendar is unchanged."
        )
    try:
        verifier = cipher().decrypt(authorization.encrypted_verifier.encode()).decode()
    except InvalidToken as exc:
        raise CalendarError(
            "Please start your Google Calendar connection again."
        ) from exc
    token = provider_request(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": settings.CRM_CALENDAR_GOOGLE_CLIENT_ID,
            "client_secret": settings.CRM_CALENDAR_GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.CRM_CALENDAR_REDIRECT_URI,
            "grant_type": "authorization_code",
            "code": request.GET["code"],
            "code_verifier": verifier,
        },
    )
    try:
        claims = id_token.verify_oauth2_token(
            token.get("id_token", ""),
            Request(),
            settings.CRM_CALENDAR_GOOGLE_CLIENT_ID,
        )
    except (ValueError, GoogleAuthError, requests.RequestException) as exc:
        raise CalendarError(
            "Google account verification failed. Please reconnect."
        ) from exc
    if (
        claims.get("nonce") != authorization.nonce
        or claims.get("email_verified") is not True
        or not isinstance(claims.get("email"), str)
        or not claims.get("sub")
    ):
        raise CalendarError("Google account verification failed. Please reconnect.")
    refresh_token = token.get("refresh_token")
    access_token = token.get("access_token")
    if (
        SCOPE not in str(token.get("scope", "")).split()
        or not isinstance(refresh_token, str)
        or not refresh_token
        or not isinstance(access_token, str)
        or not access_token
    ):
        raise CalendarError(
            "Approve calendar availability access to connect your Google Calendar."
        )
    now = timezone.now()
    # Prove the Calendar API is enabled and this grant works before replacing a connection.
    freebusy(access_token, now, now + timedelta(days=1))
    with transaction.atomic():
        CustomUser.objects.select_for_update().get(pk=request.user.pk)
        HostCalendar.objects.update_or_create(
            host=request.user,
            defaults={
                "encrypted_google_refresh_token": cipher()
                .encrypt(refresh_token.encode())
                .decode(),
                "google_email": claims["email"],
                "encrypted_url": "",
                "last_checked_at": now,
                "last_error": "",
            },
        )


def freebusy(
    access_token: str, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    result = provider_request(
        "https://www.googleapis.com/calendar/v3/freeBusy",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": "primary"}],
        },
    )
    try:
        calendar = result["calendars"]["primary"]
        if calendar.get("errors") or not isinstance(calendar["busy"], list):
            raise ValueError
        periods = []
        for item in calendar["busy"]:
            begins, ends = (
                datetime.fromisoformat(item["start"]),
                datetime.fromisoformat(item["end"]),
            )
            if timezone.is_naive(begins) or timezone.is_naive(ends) or ends <= begins:
                raise ValueError
            periods.append((begins, ends))
        return periods
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CalendarError(
            "Google Calendar availability could not be checked safely. Please reconnect."
        ) from exc


def busy_periods(
    profile: HostCalendar, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    require_configured()
    try:
        refresh_token = (
            cipher().decrypt(profile.encrypted_google_refresh_token.encode()).decode()
        )
    except InvalidToken as exc:
        raise CalendarError(
            "Reconnect Google Calendar to restore availability checks."
        ) from exc
    token = provider_request(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": settings.CRM_CALENDAR_GOOGLE_CLIENT_ID,
            "client_secret": settings.CRM_CALENDAR_GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    access_token = token.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise CalendarError("Reconnect Google Calendar to restore availability checks.")
    return freebusy(access_token, start, end)
