import base64
import binascii
import json
import uuid
from collections.abc import Callable
from functools import wraps
from io import BytesIO
from typing import Any, cast
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import connection, transaction
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from PIL import Image, UnidentifiedImageError

from apps.crm.models import (
    Lead,
    NewsletterCampaign,
    NewsletterDelivery,
    NewsletterSubscription,
)
from apps.crm.newsletters import (
    NewsletterSendError,
    duplicate_campaign,
    link_subscription_to_contact,
    newsletter_delivery_configuration_errors,
    newsletter_from_email,
    send_newsletter_campaign,
    send_newsletter_test,
    subscribe_contact,
    unsubscribe_subscription,
)
from apps.crm_email import automated
from apps.crm_email.automated import text_to_html
from apps.crm_email.forms import (
    AutomatedEmailForm,
    ComposeForm,
    NewsletterForm,
    SignatureForm,
    TemplateForm,
)
from apps.crm_email.google import Gmail, authorization_url, connect
from apps.crm_email.models import (
    Attachment,
    AutomatedEmail,
    AutomatedEmailImage,
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
    plain_text,
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
            "active_tab": "mailbox",
            **hub_context(request),
        },
    )


def hub_context(request: EmailRequest) -> dict[str, Any]:
    """Shared context for the Email & notifications tab bar."""
    if not request.user.can_manage_crm_users:
        return {}
    return {"notification_count": len(automated.specs())}


def automated_groups() -> list[dict[str, Any]]:
    copies = automated.all_copies()
    return [
        {
            "name": group,
            "slug": slugify(group),
            "emails": [copy for copy in copies if copy.spec.group == group],
        }
        for group in automated.groups()
    ]


@crm_view
@require_GET
def notifications_view(request: EmailRequest) -> HttpResponse:
    """Notifications tab: every automated email notification, grouped and editable."""
    if not request.user.can_manage_crm_users:
        raise PermissionDenied
    groups = automated_groups()
    copies = [copy for group in groups for copy in group["emails"]]
    customized = sum(1 for copy in copies if copy.customized)
    latest = (
        AutomatedEmail.objects.select_related("updated_by")
        .order_by("-updated_at")
        .first()
    )
    return render(
        request,
        "crm/email_notifications.html",
        {
            "active_tab": "notifications",
            "groups": groups,
            "total": len(copies),
            "customized": customized,
            "default_count": len(copies) - customized,
            "latest": latest,
            **hub_context(request),
        },
    )


@crm_view
@require_GET
def newsletter_list(request: EmailRequest) -> HttpResponse:
    """Newsletter workspace: campaigns and the subscriber list."""
    if not request.user.can_manage_crm_users:
        raise PermissionDenied
    campaigns = NewsletterCampaign.objects.select_related("sent_by", "created_by")
    tab = request.GET.get("tab", "campaigns")
    if tab not in {"campaigns", "subscribers"}:
        tab = "campaigns"
    status = request.GET.get("status", "")
    if status not in {
        "",
        NewsletterSubscription.Status.ACTIVE,
        NewsletterSubscription.Status.UNSUBSCRIBED,
    }:
        status = ""
    query = request.GET.get("q", "").strip()[:120]
    subscribers = NewsletterSubscription.objects.select_related("lead")
    if status:
        subscribers = subscribers.filter(status=status)
    if query:
        subscribers = subscribers.filter(
            Q(email__icontains=query) | Q(name__icontains=query)
        )
    return render(
        request,
        "crm/newsletter_list.html",
        {
            "active_tab": "newsletter",
            "newsletter_tab": tab,
            "campaigns": campaigns[:50],
            "sent_count": campaigns.filter(
                status=NewsletterCampaign.Status.SENT
            ).count(),
            "draft_count": campaigns.filter(
                status=NewsletterCampaign.Status.DRAFT
            ).count(),
            "last_sent": campaigns.filter(sent_at__isnull=False)
            .order_by("-sent_at")
            .first(),
            "subscriber_rows": subscribers[:200],
            "subscriber_query": query,
            "subscriber_status": status,
            "unsubscribed_count": NewsletterSubscription.objects.filter(
                status=NewsletterSubscription.Status.UNSUBSCRIBED
            ).count(),
            "from_email": newsletter_from_email(),
            **newsletter_context(),
            **hub_context(request),
        },
    )


@crm_view
@require_http_methods(["GET", "POST"])
def automated_email_view(request: EmailRequest, key: str) -> HttpResponse:
    """Administrators edit the wording of one automated email, or restore its default."""
    if not request.user.can_manage_crm_users:
        raise PermissionDenied
    try:
        spec = automated.spec_for(key)
    except LookupError as exc:
        raise Http404 from exc
    current = automated.copy_for(key)
    if request.method == "POST" and request.POST.get("action") == "restore":
        AutomatedEmail.objects.filter(key=key).delete()
        AuditLog.objects.create(
            actor=request.user,
            action="crm.automated_email.restored",
            entity_type="crm_email.AutomatedEmail",
            entity_id=key,
        )
        messages.success(request, f"{spec.name}: default wording restored.")
        return redirect("crm_email_notifications")
    form = AutomatedEmailForm(
        spec,
        request.POST or None,
        initial={
            name: current.html(name) if name in automated.RICH_FIELDS else current[name]
            for name in spec.fields
        },
    )
    if request.method == "POST" and form.is_valid():
        values = {name: form.cleaned_data[name] for name in spec.fields}
        with transaction.atomic():
            row, _ = AutomatedEmail.objects.select_for_update().get_or_create(key=key)
            for name, value in values.items():
                setattr(row, name, value)
            row.updated_by = request.user
            row.save()
            AuditLog.objects.create(
                actor=request.user,
                action="crm.automated_email.saved",
                entity_type="crm_email.AutomatedEmail",
                entity_id=key,
                before=dict(current.values),
                after=values,
            )
        messages.success(request, f"{spec.name}: wording saved.")
        return redirect("crm_email_notifications")
    # The editor always submits HTML for rich fields, so preview them as HTML.
    shown = automated.AutomatedEmailCopy(
        spec, {name: str(form[name].value() or "") for name in spec.fields}, True
    )
    response = render(
        request,
        "crm/automated_email.html",
        {
            "form": form,
            "spec": spec,
            "copy": current,
            "preview": automated.preview(shown),
            "placeholders": spec.placeholders.items(),
            "editor_config": {
                "placeholders": dict(spec.placeholders),
                "sample": dict(spec.sample),
                "uploadUrl": reverse("crm_email_automated_image_upload"),
                "images": [
                    {
                        "url": image_url(request, image),
                        "name": image.name,
                        "width": image.width,
                        "height": image.height,
                    }
                    for image in AutomatedEmailImage.objects.all()[:40]
                ],
            },
            "row": AutomatedEmail.objects.filter(key=key)
            .select_related("updated_by")
            .first(),
        },
    )
    response["Cache-Control"] = "private, no-store"
    return response


IMAGE_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}
IMAGE_MAX_BYTES = 3 * 1024 * 1024


def image_url(request: EmailRequest, image: AutomatedEmailImage) -> str:
    """Public address recipients' mail clients fetch; prefers the configured site URL."""
    path = reverse("crm_email_automated_image", args=[image.pk])
    public = settings.PUBLIC_APP_URL.rstrip("/")
    if public.startswith("https://"):
        return public + path
    return request.build_absolute_uri(path)


@crm_view
@require_POST
def automated_image_upload(request: EmailRequest) -> HttpResponse:
    """Store an editor image and return the public URL to embed in the email."""
    if not request.user.can_manage_crm_users:
        raise PermissionDenied
    upload = request.FILES.get("image")
    if upload is None:
        return JsonResponse({"error": "Choose an image file."}, status=400)
    if (upload.size or 0) > IMAGE_MAX_BYTES:
        return JsonResponse({"error": "Images must be 3 MB or smaller."}, status=400)
    data = upload.read()
    try:
        with Image.open(BytesIO(data)) as parsed:
            parsed.verify()
        with Image.open(BytesIO(data)) as parsed:
            kind, width, height = parsed.format or "", parsed.width, parsed.height
    except (UnidentifiedImageError, OSError, ValueError):
        return JsonResponse(
            {"error": "Use a PNG, JPEG, GIF or WebP image."}, status=400
        )
    if kind not in IMAGE_TYPES:
        return JsonResponse(
            {"error": "Use a PNG, JPEG, GIF or WebP image."}, status=400
        )
    image = AutomatedEmailImage.objects.create(
        name=str(upload.name or "image")[:255],
        content_type=IMAGE_TYPES[kind],
        size=len(data),
        width=width,
        height=height,
        data=data,
        uploaded_by=request.user,
    )
    AuditLog.objects.create(
        actor=request.user,
        action="crm.automated_email.image_uploaded",
        entity_type="crm_email.AutomatedEmailImage",
        entity_id=str(image.pk),
        after={"name": image.name, "size": image.size},
    )
    return JsonResponse(
        {
            "url": image_url(request, image),
            "name": image.name,
            "width": width,
            "height": height,
        }
    )


@require_GET
def automated_image(request: HttpRequest, image_id: uuid.UUID) -> HttpResponse:
    """Serve an uploaded email image. Public: recipients' mail clients are not signed in."""
    image = get_object_or_404(AutomatedEmailImage, pk=image_id)
    response = HttpResponse(bytes(image.data), content_type=image.content_type)
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def newsletter_context() -> dict[str, Any]:
    return {
        "newsletters": NewsletterCampaign.objects.select_related("sent_by")[:25],
        "newsletter_subscribers": NewsletterSubscription.objects.filter(
            status=NewsletterSubscription.Status.ACTIVE
        ).count(),
    }


def newsletter_editor_config(request: EmailRequest) -> dict[str, Any]:
    return {
        "placeholders": {},
        "sample": {},
        "uploadUrl": reverse("crm_email_automated_image_upload"),
        "images": [
            {
                "url": image_url(request, image),
                "name": image.name,
                "width": image.width,
                "height": image.height,
            }
            for image in AutomatedEmailImage.objects.all()[:40]
        ],
    }


@crm_view
@require_http_methods(["GET", "POST"])
def newsletter_edit(
    request: EmailRequest, campaign_id: int | None = None
) -> HttpResponse:
    """Compose a newsletter with the rich editor; drafts only are editable."""
    if not request.user.can_manage_crm_users:
        raise PermissionDenied
    campaign = (
        get_object_or_404(NewsletterCampaign, pk=campaign_id) if campaign_id else None
    )
    editable = campaign is None or campaign.status == NewsletterCampaign.Status.DRAFT
    if request.method == "POST" and request.POST.get("action") == "delete":
        if campaign is None or not editable:
            raise PermissionDenied
        campaign.delete()
        messages.success(request, "Newsletter draft deleted.")
        return redirect_settings("newsletters")
    initial = (
        {
            "subject": campaign.subject,
            "preview_text": campaign.preview_text,
            "body_html": campaign.body_html or text_to_html(campaign.body),
        }
        if campaign
        else {}
    )
    form = NewsletterForm(
        request.POST if request.method == "POST" and editable else None,
        initial=initial,
    )
    if request.method == "POST" and editable and form.is_valid():
        with transaction.atomic():
            campaign = campaign or NewsletterCampaign(created_by=request.user)
            campaign.subject = form.cleaned_data["subject"]
            campaign.preview_text = form.cleaned_data["preview_text"]
            campaign.body_html = form.cleaned_data["body_html"]
            campaign.body = plain_text(campaign.body_html).strip()
            campaign.full_clean()
            campaign.save()
            AuditLog.objects.create(
                actor=request.user,
                action="crm.newsletter.saved",
                entity_type="crm.NewsletterCampaign",
                entity_id=str(campaign.pk),
                after={"subject": campaign.subject},
            )
        messages.success(request, "Newsletter draft saved.")
        if request.POST.get("action") == "send":
            return redirect("crm_newsletter_send", campaign.pk)
        return redirect("crm_newsletter", campaign.pk)
    if not editable:
        for field in form.fields.values():
            field.disabled = True
    shown_html = str(form["body_html"].value() or "")
    response = render(
        request,
        "crm/newsletter_edit.html",
        {
            "form": form,
            "campaign": campaign,
            "editable": editable,
            "preview_html": shown_html,
            "editor_config": newsletter_editor_config(request),
            "deliveries": campaign.deliveries.order_by("recipient_email")[:200]
            if campaign
            else [],
            "from_email": newsletter_from_email(),
            **newsletter_context(),
        },
    )
    response["Cache-Control"] = "private, no-store"
    return response


@crm_view
@require_http_methods(["GET", "POST"])
def newsletter_send(request: EmailRequest, campaign_id: int) -> HttpResponse:
    """Review recipients, send a test to yourself, or send to every active subscriber."""
    if not request.user.can_manage_crm_users:
        raise PermissionDenied
    campaign = get_object_or_404(NewsletterCampaign, pk=campaign_id)
    configuration = newsletter_delivery_configuration_errors()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "test":
            try:
                send_newsletter_test(campaign, request.user.email)
            except Exception as exc:  # noqa: BLE001 - backend-specific errors
                messages.error(request, f"Test email failed: {type(exc).__name__}.")
            else:
                messages.success(request, f"Test email sent to {request.user.email}.")
            return redirect("crm_newsletter_send", campaign.pk)
        if action == "send":
            if campaign.status == NewsletterCampaign.Status.SENDING:
                messages.error(request, "This newsletter is already being sent.")
                return redirect("crm_newsletter_send", campaign.pk)
            try:
                campaign = send_newsletter_campaign(campaign.pk, sent_by=request.user)
            except NewsletterSendError as exc:
                messages.error(request, str(exc))
            else:
                AuditLog.objects.create(
                    actor=request.user,
                    action="crm.newsletter.sent",
                    entity_type="crm.NewsletterCampaign",
                    entity_id=str(campaign.pk),
                    after={
                        "delivered": campaign.delivered_count,
                        "failed": campaign.failed_count,
                    },
                )
                if campaign.failed_count:
                    messages.warning(
                        request,
                        f"Newsletter sent to {campaign.delivered_count} recipients; "
                        f"{campaign.failed_count} deliveries can be retried.",
                    )
                else:
                    messages.success(
                        request,
                        f"Newsletter sent to {campaign.delivered_count} recipients.",
                    )
            return redirect("crm_newsletter", campaign.pk)
    response = render(
        request,
        "crm/newsletter_send.html",
        {
            "campaign": campaign,
            "configuration_errors": configuration,
            "retry_count": campaign.deliveries.filter(
                status=NewsletterDelivery.Status.FAILED
            ).count(),
            "preview_html": campaign.body_html or text_to_html(campaign.body),
            "from_email": newsletter_from_email(),
            "skipped_count": campaign.deliveries.filter(
                status=NewsletterDelivery.Status.SKIPPED
            ).count(),
            **newsletter_context(),
        },
    )
    response["Cache-Control"] = "private, no-store"
    return response


@crm_view
@require_POST
def newsletter_duplicate(request: EmailRequest, campaign_id: int) -> HttpResponse:
    if not request.user.can_manage_crm_users:
        raise PermissionDenied
    campaign = get_object_or_404(NewsletterCampaign, pk=campaign_id)
    copy = duplicate_campaign(campaign, created_by=request.user)
    AuditLog.objects.create(
        actor=request.user,
        action="crm.newsletter.duplicated",
        entity_type="crm.NewsletterCampaign",
        entity_id=str(copy.pk),
        after={"source": campaign.pk, "subject": copy.subject},
    )
    messages.success(request, "A new draft was created from this newsletter.")
    return redirect("crm_newsletter", copy.pk)


@crm_view
@require_POST
def newsletter_subscriber(request: EmailRequest) -> HttpResponse:
    """Add an address with recorded consent, or unsubscribe an existing one."""
    if not request.user.can_manage_crm_users:
        raise PermissionDenied
    action = request.POST.get("action")
    if action == "unsubscribe":
        subscription = get_object_or_404(
            NewsletterSubscription, pk=request.POST.get("subscription_id")
        )
        unsubscribe_subscription(subscription)
        AuditLog.objects.create(
            actor=request.user,
            action="crm.newsletter.unsubscribed",
            entity_type="crm.NewsletterSubscription",
            entity_id=str(subscription.pk),
            after={"email": subscription.email},
        )
        messages.success(request, f"{subscription.email} is unsubscribed.")
        return redirect(f"{reverse('crm_newsletter_list')}?tab=subscribers")
    if action != "subscribe":
        messages.error(request, "Choose whether to add or remove a subscriber.")
        return redirect(f"{reverse('crm_newsletter_list')}?tab=subscribers")
    email = request.POST.get("email", "").strip().lower()
    try:
        if len(email) > 254:
            raise ValidationError("Email address is too long.")
        validate_email(email)
    except ValidationError:
        messages.error(request, "Enter a valid email address.")
        return redirect(f"{reverse('crm_newsletter_list')}?tab=subscribers")
    if request.POST.get("consent") != "yes":
        messages.error(
            request, "Confirm that this person agreed to receive the newsletter."
        )
        return redirect(f"{reverse('crm_newsletter_list')}?tab=subscribers")
    lead = (
        Lead.objects.filter(is_deleted=False, contact_email__iexact=email)
        .order_by("-updated_at")
        .first()
    )
    if lead is not None and lead.contact_email.strip().lower() == email:
        subscription = subscribe_contact(lead, consented=True)
    else:
        name = request.POST.get("name", "").strip()[:255]
        subscription, _created = NewsletterSubscription.objects.update_or_create(
            email=email,
            defaults={
                "name": name,
                "status": NewsletterSubscription.Status.ACTIVE,
                "consented_at": timezone.now(),
                "unsubscribed_at": None,
                "source_path": "/crm/email/newsletters/",
            },
        )
        link_subscription_to_contact(subscription)
    AuditLog.objects.create(
        actor=request.user,
        action="crm.newsletter.subscribed",
        entity_type="crm.NewsletterSubscription",
        entity_id=str(subscription.pk),
        after={"email": subscription.email, "lead_id": subscription.lead_id},
    )
    messages.success(request, f"{subscription.email} is on the newsletter.")
    return redirect(f"{reverse('crm_newsletter_list')}?tab=subscribers")


def redirect_settings(anchor: str) -> HttpResponse:
    return redirect(
        "crm_newsletter_list" if anchor == "newsletters" else "crm_email_settings"
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
    if request.GET.get("state", "").startswith("calendar."):
        from apps.crm.calendar_views import google_calendar_callback

        return google_calendar_callback(request)
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
    if message.lead_id is None:
        raise PermissionDenied
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
