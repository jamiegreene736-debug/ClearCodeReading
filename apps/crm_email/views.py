import base64
import binascii
import json
import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any, cast
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from apps.crm.models import Lead
from apps.crm_email.forms import ComposeForm, SignatureForm, TemplateForm
from apps.crm_email.google import Gmail, authorization_url, connect
from apps.crm_email.models import (
    Attachment,
    Conversation,
    EmailTemplate,
    Mailbox,
    Message,
    WorkerHeartbeat,
)
from apps.crm_email.security import (
    EmailError,
    EmailRequest,
    clean_html,
    configuration_errors,
    decrypt,
    mailbox_lock,
    require_configured,
    require_crm,
)
from apps.crm_email.services import (
    FINAL_STATUSES,
    active_mailbox,
    disconnect,
    save_message,
    worker_healthy,
)
from apps.crm_email.sync import headers, provider_id
from apps.users.models import AuditLog


def crm_view(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    @wraps(view)
    @login_required
    def wrapped(request: EmailRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        require_crm(request.user)
        with timezone.override(ZoneInfo("America/New_York")):
            try:
                return view(request, *args, **kwargs)
            except EmailError as exc:
                messages.error(request, str(exc))
                return redirect("crm_email_settings")

    return wrapped


def lead_for(pk: int) -> Lead:
    # Matches current CRM contact visibility; do not invent a second ownership policy here.
    return get_object_or_404(Lead, pk=pk, is_deleted=False)


@crm_view
@require_http_methods(["GET", "POST"])
def settings_view(request: EmailRequest) -> HttpResponse:
    mailbox, _ = Mailbox.objects.get_or_create(user=request.user)
    form = SignatureForm(request.POST or None, initial={"signature": mailbox.signature})
    if request.method == "POST" and form.is_valid():
        Mailbox.objects.filter(pk=mailbox.pk).update(
            signature=form.cleaned_data["signature"]
        )
        messages.success(request, "Email signature saved.")
        return redirect("crm_email_settings")
    return render(
        request,
        "crm/email_settings.html",
        {
            "mailbox": mailbox,
            "form": form,
            "configured": settings.CRM_EMAIL_ENABLED and not configuration_errors(),
            "worker_healthy": worker_healthy(),
            "heartbeat": WorkerHeartbeat.objects.filter(name="email").first(),
            "setup_errors": configuration_errors()
            if request.user.can_manage_crm_users
            else [],
            "team_mailboxes": Mailbox.objects.select_related("user").order_by(
                "user__email"
            )
            if request.user.can_manage_crm_users
            else [],
            "templates": EmailTemplate.objects.filter(owner=request.user),
        },
    )


@crm_view
@require_POST
def connect_view(request: EmailRequest) -> HttpResponse:
    try:
        return redirect(authorization_url(request))
    except EmailError as exc:
        messages.error(request, str(exc))
        return redirect("crm_email_settings")


@crm_view
@require_GET
def callback(request: EmailRequest) -> HttpResponse:
    try:
        connect(request)
        messages.success(
            request,
            "Google mailbox connected. Linked conversations will synchronize in the background.",
        )
    except (EmailError, GoogleAuthError) as exc:
        messages.error(
            request,
            str(exc)
            if isinstance(exc, EmailError)
            else "Google identity could not be verified. Please try again.",
        )
    return redirect("crm_email_settings")


@crm_view
@require_POST
def disconnect_view(request: EmailRequest, mailbox_id: int) -> HttpResponse:
    mailbox = get_object_or_404(Mailbox, pk=mailbox_id)
    revoked = disconnect(mailbox, request.user)
    messages.success(request, "Mailbox disconnected. Linked CRM history is retained.")
    if not revoked:
        messages.warning(
            request,
            "Google revocation was not confirmed. Remove CRM access from your Google account's connections page.",
        )
    return redirect("crm_email_settings")


@crm_view
@require_GET
def contact_email(request: EmailRequest, pk: int) -> HttpResponse:
    lead = lead_for(pk)
    conversations = lead.email_conversations.filter(
        import_pending=False
    ).select_related("mailbox__user")
    page = Paginator(conversations, 20).get_page(request.GET.get("page"))
    own_messages = (
        Message.objects.filter(lead=lead, mailbox__user=request.user)
        .exclude(status__in=FINAL_STATUSES)
        .order_by("-created_at")[:50]
    )
    return render(
        request,
        "crm/contact_email.html",
        {
            "lead": lead,
            "page": page,
            "own_messages": own_messages,
            "imports": lead.email_conversations.filter(
                mailbox__user=request.user, import_pending=True
            ),
            "mailbox": Mailbox.objects.filter(user=request.user).first(),
        },
    )


@crm_view
@require_GET
def conversation_view(
    request: EmailRequest, pk: int, conversation_id: int
) -> HttpResponse:
    lead = lead_for(pk)
    conversation = get_object_or_404(
        Conversation, pk=conversation_id, lead=lead, import_pending=False
    )
    page = Paginator(
        conversation.messages.filter(status__in=FINAL_STATUSES)
        .prefetch_related("attachments")
        .order_by("-sent_at", "-created_at"),
        20,
    ).get_page(request.GET.get("page"))
    return render(
        request,
        "crm/email_thread.html",
        {"lead": lead, "conversation": conversation, "page": page},
    )


@crm_view
@require_http_methods(["GET", "POST"])
def compose(request: EmailRequest, pk: int) -> HttpResponse:
    lead = lead_for(pk)
    mailbox, _ = Mailbox.objects.get_or_create(user=request.user)
    initial: dict[str, Any] = {
        "draft_id": uuid.uuid4(),
        "to": lead.contact_email,
        "body_html": "<p></p>" + clean_html(mailbox.signature),
        "follow_up_days": 0,
    }
    draft = None
    try:
        if request.GET.get("draft"):
            draft = get_object_or_404(
                Message,
                pk=request.GET["draft"],
                lead=lead,
                mailbox=mailbox,
                status=Message.Status.DRAFT,
            )
            initial = {
                name: getattr(draft, name)
                for name in ["subject", "body_html", "follow_up_days", "scheduled_at"]
            }
            initial.update(
                {name: ", ".join(getattr(draft, name)) for name in ["to", "cc", "bcc"]}
            )
            initial["draft_id"] = draft.pk
        elif request.GET.get("reply"):
            reply = get_object_or_404(
                Message,
                pk=request.GET["reply"],
                lead=lead,
                mailbox=mailbox,
                status__in=FINAL_STATUSES,
            )
            initial.update(reply_id=reply.pk, subject=reply.subject)
            own_address = mailbox.email.lower()
            recipients = (
                reply.to
                if reply.status == Message.Status.SENT
                else [reply.reply_to or reply.sender]
            )
            initial["to"] = ", ".join(
                address for address in recipients if address.lower() != own_address
            )
            if request.GET.get("all") == "1":
                initial["cc"] = ", ".join(
                    dict.fromkeys(
                        address
                        for address in reply.to + reply.cc
                        if address.lower() != own_address and address not in recipients
                    )
                )
        elif request.GET.get("template"):
            template = get_object_or_404(
                EmailTemplate, pk=request.GET["template"], owner=request.user
            )
            initial.update(
                subject=template.subject,
                body_html=clean_html(template.body_html)
                + clean_html(mailbox.signature),
            )
    except (ValueError, ValidationError):
        raise PermissionDenied
    form = ComposeForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        action = request.POST.get("action", "draft")
        if action not in {"draft", "send"}:
            raise PermissionDenied
        try:
            message = save_message(
                request.user, lead, form, request.FILES.getlist("attachments"), action
            )
            messages.success(
                request,
                "Draft saved."
                if message.status == Message.Status.DRAFT
                else "Email queued. Check its status on this contact.",
            )
            return redirect("crm_contact_email", pk=pk)
        except EmailError as exc:
            form.add_error(None, str(exc))
    return render(
        request,
        "crm/email_compose.html",
        {
            "lead": lead,
            "form": form,
            "mailbox": mailbox,
            "draft": draft,
            "templates": EmailTemplate.objects.filter(owner=request.user),
        },
    )


@crm_view
@require_POST
def cancel_message(
    request: EmailRequest, pk: int, message_id: uuid.UUID
) -> HttpResponse:
    lead = lead_for(pk)
    message = get_object_or_404(
        Message, pk=message_id, lead=lead, mailbox__user=request.user
    )
    with mailbox_lock(message.mailbox_id):
        cancelled = Message.objects.filter(
            pk=message.pk, status__in=[Message.Status.DRAFT, Message.Status.QUEUED]
        ).update(status=Message.Status.CANCELLED)
    if cancelled:
        AuditLog.objects.create(
            actor=request.user,
            action="crm.email.cancelled",
            entity_type="Message",
            entity_id=str(message.pk),
        )
        messages.success(request, "Email cancelled.")
    else:
        messages.error(
            request, "This email has already started sending and cannot be cancelled."
        )
    return redirect("crm_contact_email", pk=pk)


@crm_view
@require_POST
def sync_view(request: EmailRequest) -> HttpResponse:
    try:
        mailbox = active_mailbox(request.user)
        Mailbox.objects.filter(pk=mailbox.pk).update(sync_requested_at=timezone.now())
        messages.success(request, "Synchronization requested.")
    except EmailError as exc:
        messages.error(request, str(exc))
    return redirect("crm_email_settings")


@crm_view
@require_GET
def download(request: EmailRequest, attachment_id: int) -> HttpResponse:
    attachment = get_object_or_404(
        Attachment.objects.select_related(
            "message__mailbox", "message__lead", "message__conversation"
        ),
        pk=attachment_id,
    )
    message = attachment.message
    lead_for(message.lead_id)
    if message.mailbox.user_id != request.user.pk and (
        message.status not in FINAL_STATUSES
        or message.conversation is None
        or message.conversation.import_pending
    ):
        raise PermissionDenied
    try:
        content = decrypt(bytes(attachment.encrypted_data))
    except EmailError:
        return HttpResponse(
            "Attachment temporarily unavailable. Contact your administrator.",
            status=503,
        )
    response = HttpResponse(content, content_type="application/octet-stream")
    from django.utils.http import content_disposition_header

    response["Content-Disposition"] = content_disposition_header(
        True, attachment.filename
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response


@crm_view
@require_POST
def remove_attachment(request: EmailRequest, attachment_id: int) -> HttpResponse:
    attachment = get_object_or_404(
        Attachment.objects.select_related("message"),
        pk=attachment_id,
        message__mailbox__user=request.user,
    )
    with mailbox_lock(attachment.message.mailbox_id):
        attachment.message.refresh_from_db()
        if attachment.message.status != Message.Status.DRAFT:
            raise PermissionDenied
        attachment.delete()
    return redirect("crm_contact_email", pk=attachment.message.lead_id)


@crm_view
@require_http_methods(["GET", "POST"])
def template_view(
    request: EmailRequest, template_id: int | None = None
) -> HttpResponse:
    template = (
        get_object_or_404(EmailTemplate, pk=template_id, owner=request.user)
        if template_id
        else None
    )
    if request.method == "POST" and request.POST.get("action") == "delete" and template:
        template.delete()
        return redirect("crm_email_settings")
    initial = (
        {name: getattr(template, name) for name in ["name", "subject", "body_html"]}
        if template
        else {}
    )
    form = TemplateForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        template = template or EmailTemplate(owner=request.user)
        for name, value in form.cleaned_data.items():
            setattr(template, name, value)
        template.save()
        messages.success(request, "Email template saved.")
        return redirect("crm_email_settings")
    return render(
        request, "crm/email_template.html", {"form": form, "template": template}
    )


@crm_view
@require_http_methods(["GET", "POST"])
def import_view(request: EmailRequest, pk: int) -> HttpResponse:
    lead = lead_for(pk)
    candidates = []
    error = ""
    if request.method == "POST":
        try:
            require_configured()
            mailbox = active_mailbox(request.user)
            with mailbox_lock(mailbox.pk):
                mailbox.refresh_from_db()
                if mailbox.status != Mailbox.Status.CONNECTED:
                    raise EmailError("Reconnect your mailbox before importing.")
                if request.POST.get("selection"):
                    selection = signing.loads(
                        request.POST["selection"], salt="crm-email-import", max_age=600
                    )
                    if (
                        selection["user"] != request.user.pk
                        or selection["lead"] != lead.pk
                        or selection["mailbox"] != mailbox.pk
                    ):
                        raise PermissionDenied
                    thread_id = provider_id(selection["thread"])
                    conversation, _created = Conversation.objects.get_or_create(
                        mailbox=mailbox,
                        gmail_thread_id=thread_id,
                        defaults={
                            "lead": lead,
                            "import_pending": True,
                            "subject": selection["subject"],
                        },
                    )
                    if conversation.lead_id != lead.pk:
                        raise EmailError(
                            "This thread is already linked to another contact. Review that record before importing."
                        )
                    Mailbox.objects.filter(pk=mailbox.pk).update(
                        sync_requested_at=timezone.now()
                    )
                    AuditLog.objects.create(
                        actor=request.user,
                        action="crm.email.import_requested",
                        entity_type="Conversation",
                        entity_id=str(conversation.pk),
                    )
                    messages.success(
                        request, "Selected conversation queued for import."
                    )
                    return redirect("crm_contact_email", pk=pk)
                validate_email(lead.contact_email)
                client = Gmail(mailbox)
                address = json.dumps(lead.contact_email)
                result = client.request(
                    "GET",
                    "threads",
                    params={
                        "q": f"newer_than:90d {{from:{address} to:{address}}}",
                        "maxResults": 20,
                    },
                )
                for item in result.get("threads", []):
                    thread_id = provider_id(item.get("id"))
                    data = client.request(
                        "GET",
                        "threads/" + thread_id,
                        params={
                            "format": "metadata",
                            "metadataHeaders": ["Subject", "From", "To"],
                        },
                    )
                    thread_messages = data.get("messages", [])
                    if not thread_messages:
                        continue
                    subject = headers(thread_messages[0].get("payload", {})).get(
                        "subject", "(No subject)"
                    )[:998]
                    candidates.append(
                        {
                            "subject": subject,
                            "count": len(thread_messages),
                            "selection": signing.dumps(
                                {
                                    "user": request.user.pk,
                                    "lead": lead.pk,
                                    "mailbox": mailbox.pk,
                                    "thread": thread_id,
                                    "subject": subject,
                                },
                                salt="crm-email-import",
                            ),
                        }
                    )
        except (EmailError, signing.BadSignature, ValidationError) as exc:
            error = (
                str(exc)
                if isinstance(exc, EmailError)
                else "Import selection expired or the contact email is invalid. Search again."
            )
    return render(
        request,
        "crm/email_import.html",
        {
            "lead": lead,
            "candidates": candidates,
            "error": error,
            "searched": request.method == "POST",
        },
    )


@csrf_exempt
@require_POST
def push(request: EmailRequest) -> HttpResponse:
    if (
        getattr(connection, "schema_name", "") != "public"
        or not settings.CRM_EMAIL_ENABLED
    ):
        return HttpResponse(status=503)
    if not settings.CRM_EMAIL_PUBSUB_EMAIL or not settings.CRM_EMAIL_PUBSUB_AUDIENCE:
        return HttpResponse(status=503)
    token = request.headers.get("Authorization", "")
    if not token.startswith("Bearer ") or len(request.body) > 20000:
        return HttpResponse(status=403)
    try:
        claims = cast(Callable[..., dict[str, Any]], id_token.verify_oauth2_token)(
            token[7:], GoogleRequest(), settings.CRM_EMAIL_PUBSUB_AUDIENCE
        )
        if (
            claims.get("email") != settings.CRM_EMAIL_PUBSUB_EMAIL
            or claims.get("email_verified") is not True
        ):
            return HttpResponse(status=403)
        envelope = json.loads(request.body)
        data = json.loads(base64.b64decode(envelope["message"]["data"], validate=True))
        email = data["emailAddress"]
        history_id = str(data["historyId"])
        if not isinstance(email, str) or not history_id.isdigit():
            return HttpResponse(status=400)
    except (ValueError, KeyError, TypeError, binascii.Error, GoogleAuthError):
        return HttpResponse(status=403)
    # Notifications contain only a hint; canonical changes are fetched from Gmail by the worker.
    Mailbox.objects.filter(email__iexact=email, status=Mailbox.Status.CONNECTED).update(
        sync_requested_at=timezone.now()
    )
    return HttpResponse(status=204)
