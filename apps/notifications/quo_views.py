"""Phone ownership and signed Quo callbacks for two-way board messaging."""

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.notifications.board_sms import RELAY_NUMBER
from apps.users.models import BoardTextAccount, BoardTextReceipt
from apps.users.onboarding_views import PortalRequest
from apps.users.reader_board import BoardNoteForm, board_readers, post_note


def digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@login_required
@never_cache
@require_POST
def board_text_settings(request: PortalRequest, child_id: int) -> HttpResponse:
    get_object_or_404(board_readers(request.user), pk=child_id)
    if request.POST.get("action") == "disable":
        BoardTextAccount.objects.filter(user=request.user).delete()
        request.session.pop("reader_sms_join", None)
        messages.success(request, "Board texting is off for your account.")
    elif (
        request.POST.get("action") == "enable"
        and settings.QUO_API_KEY
        and settings.QUO_WEBHOOK_KEY
    ):
        code = secrets.token_hex(8).upper()
        BoardTextAccount.objects.update_or_create(
            user=request.user,
            defaults={
                "phone": None,
                "verified_at": None,
                "code_digest": digest(code),
                "code_expires_at": timezone.now() + timedelta(minutes=15),
            },
        )
        request.session["reader_sms_join"] = code
    else:
        return HttpResponse(status=400)
    return redirect("portal_reader_board", child_id=child_id)


def valid_signature(request: HttpRequest) -> bool:
    secret = settings.QUO_WEBHOOK_KEY
    if not secret or not secret.startswith("whsec_"):
        return False
    stamp = request.headers.get("webhook-timestamp", "")
    identifier = request.headers.get("webhook-id", "")
    if (
        not identifier
        or not re.fullmatch(r"[0-9]{1,12}", stamp)
        or abs(time.time() - int(stamp)) > 300
    ):
        return False
    try:
        key = base64.b64decode(secret[6:], validate=True)
    except (ValueError, binascii.Error):
        return False
    if not key:
        return False
    signed = f"{identifier}.{stamp}.".encode() + request.body
    expected = b"v1," + base64.b64encode(hmac.digest(key, signed, "sha256"))
    return any(
        hmac.compare_digest(expected, item.encode())
        for item in request.headers.get("webhook-signature", "").split()
    )


@transaction.atomic
def receive_sms(sender: str, message_id: str, body: str) -> None:
    if not BoardTextReceipt.objects.get_or_create(message_id=message_id)[1]:
        return
    join = re.fullmatch(r"JOIN\s+([A-Fa-f0-9]{16})", body, re.IGNORECASE)
    if join:
        account = (
            BoardTextAccount.objects.select_for_update()
            .filter(
                code_digest=digest(join[1].upper()), code_expires_at__gt=timezone.now()
            )
            .first()
        )
        if account and board_readers(account.user).exists():
            try:
                with transaction.atomic():
                    account.phone = sender
                    account.verified_at = timezone.now()
                    account.code_digest = account.code_expires_at = None
                    account.save()
            except IntegrityError:
                # A number can represent only one portal identity.
                if not BoardTextAccount.objects.filter(phone=sender).exists():
                    raise
        return
    account = (
        BoardTextAccount.objects.select_for_update()
        .select_related("user")
        .filter(phone=sender, verified_at__isnull=False)
        .first()
    )
    if not account:
        return
    if body.upper() in {
        "STOP",
        "STOPALL",
        "UNSUBSCRIBE",
        "CANCEL",
        "END",
        "QUIT",
        "REVOKE",
        "OPT OUT",
    }:
        account.delete()
        return
    reply = re.fullmatch(r"R([1-9][0-9]{0,17})\s+(.+)", body, re.IGNORECASE | re.DOTALL)
    if not reply or not board_readers(account.user).filter(pk=int(reply[1])).exists():
        return
    form = BoardNoteForm(
        {"body": reply[2], "submission_id": uuid5(NAMESPACE_URL, "quo:" + message_id)}
    )
    if form.is_valid():
        post_note(account.user, int(reply[1]), form)


@csrf_exempt
@require_POST
def quo_webhook(request: HttpRequest) -> HttpResponse:
    if len(request.body) > 128 * 1024 or not valid_signature(request):
        return HttpResponse(status=403)
    try:
        event = json.loads(request.body)
    except (ValueError, UnicodeError):
        return HttpResponse(status=400)
    if not isinstance(event, dict) or event.get("type") != "message.received":
        return HttpResponse(status=204)
    data = event.get("data")
    if not isinstance(data, dict):
        return HttpResponse(status=400)
    resource, context = data.get("resource"), data.get("context")
    if not isinstance(resource, dict) or not isinstance(context, dict):
        return HttpResponse(status=400)
    sender, message_id, body = (
        context.get("senderIdentifier"),
        resource.get("id"),
        resource.get("text"),
    )
    if (
        context.get("recipientIdentifiers") == [RELAY_NUMBER]
        and resource.get("direction") == "incoming"
        and isinstance(sender, str)
        and re.fullmatch(r"\+[1-9][0-9]{7,14}", sender)
        and sender != RELAY_NUMBER
        and isinstance(message_id, str)
        and 0 < len(message_id) <= 120
        and isinstance(body, str)
        and 0 < len(body) <= 2100
    ):
        receive_sms(sender, message_id, body.strip())
    return HttpResponse(status=204)
