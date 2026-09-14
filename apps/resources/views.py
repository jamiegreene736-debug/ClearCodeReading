from io import BytesIO
from uuid import UUID, uuid4

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.crm.views import FAMILY_RESOURCES_SESSION_KEY
from apps.resources.access import (
    can_publish,
    editor_required,
    public_site,
    scoped_assets,
    scoped_resources,
)
from apps.resources.forms import (
    ResourceForm,
    ScheduleForm,
    validate_publication,
    video_embed,
)
from apps.resources.models import Resource, Revision, Topic
from apps.resources.services import (
    EditConflict,
    bulk_upload,
    check_version,
    create_resource,
    duplicate,
    publish,
    record_change,
    revision_values,
    save_draft,
)


def published_revisions():
    now = timezone.now()
    return (
        Revision.objects.filter(resource__archived=False)
        .filter(
            Q(resource__scheduled_id=F("pk"), resource__publish_at__lte=now)
            | (
                Q(resource__live_id=F("pk"))
                & (
                    Q(resource__scheduled__isnull=True)
                    | Q(resource__publish_at__gt=now)
                )
            )
        )
        .select_related("resource", "topic", "asset", "cover", "asset__preview")
        .defer("asset__data", "cover__data", "asset__preview__data")
    )


def library_context(request: HttpRequest) -> dict:
    revisions = published_revisions()
    query = request.GET.get("q", "").strip()[:200]
    audience = request.GET.get("audience", "")
    topic = request.GET.get("topic", "")
    kind = request.GET.get("kind", "")
    if query:
        revisions = revisions.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        )
    if audience in {"families", "educators"}:
        revisions = revisions.filter(audience__in=[audience, "both"])
    if topic.isdigit():
        revisions = revisions.filter(topic_id=topic)
    if kind in Revision.Kind.values:
        revisions = revisions.filter(kind=kind)
    # Titles and summaries are discoverable; bodies/files stay behind the selected access rule.
    return {
        "resource_page": Paginator(
            revisions.order_by("-featured", "-created_at"), 12
        ).get_page(request.GET.get("page")),
        "resource_topics": Topic.objects.all(),
        "resource_query": query,
        "selected_audience": audience,
        "selected_topic": topic,
        "selected_kind": kind,
    }


@editor_required
@require_GET
def manager(request: HttpRequest) -> HttpResponse:
    resources = scoped_resources(request.user)
    query = request.GET.get("q", "").strip()[:200]
    tab = request.GET.get("tab", "all")
    if query:
        resources = resources.filter(
            Q(draft__title__icontains=query) | Q(draft__description__icontains=query)
        )
    if tab == "archived":
        resources = resources.filter(archived=True)
    else:
        resources = resources.filter(archived=False)
        if tab == "drafts":
            resources = resources.filter(live__isnull=True).filter(
                Q(scheduled__isnull=True) | Q(publish_at__gt=timezone.now())
            )
        elif tab == "published":
            resources = resources.filter(
                Q(live__isnull=False) | Q(publish_at__lte=timezone.now())
            )
        elif tab == "review":
            resources = resources.filter(submitted=True)
        elif tab == "scheduled":
            resources = resources.filter(publish_at__gt=timezone.now())
    return render(
        request,
        "resources/manager.html",
        {
            "page": Paginator(resources, 20).get_page(request.GET.get("page")),
            "tab": tab,
            "query": query,
            "publisher": can_publish(request.user),
            "tabs": [
                ("all", "All"),
                ("drafts", "Drafts"),
                ("review", "In review"),
                ("scheduled", "Scheduled"),
                ("published", "Published"),
                ("archived", "Archived"),
            ],
        },
    )


@editor_required
@require_http_methods(["GET", "POST"])
def add(request: HttpRequest) -> HttpResponse:
    errors = []
    if request.method == "POST":
        kind = request.POST.get("kind", "")
        try:
            if kind not in Revision.Kind.values:
                raise ValidationError("Choose a file, link or article.")
            with transaction.atomic():
                if kind == Revision.Kind.FILE:
                    topic = request.POST.get("topic", "")
                    topic_obj = (
                        get_object_or_404(Topic, pk=topic) if topic.isdigit() else None
                    )
                    audience = request.POST.get("audience", "families")
                    if audience not in Revision.Audience.values:
                        raise ValidationError("Choose an audience.")
                    try:
                        token = UUID(request.POST.get("token", ""))
                    except ValueError as exc:
                        raise ValidationError(
                            "Reload this page before uploading."
                        ) from exc
                    resources = bulk_upload(
                        request.user,
                        request.FILES.getlist("files"),
                        token,
                        topic_obj,
                        audience,
                    )
                    if len(resources) == 1:
                        return redirect("resources:edit", pk=resources[0].pk)
                    messages.success(
                        request,
                        f"{len(resources)} drafts created. Open each to review and publish.",
                    )
                    return redirect("resources:manager")
                resource = create_resource(request.user, kind=kind)
                return redirect("resources:edit", pk=resource.pk)
        except ValidationError as exc:
            errors = exc.messages
    return render(
        request,
        "resources/add.html",
        {"errors": errors, "token": uuid4(), "topics": Topic.objects.all()},
    )


def editor_context(
    request: HttpRequest, resource: Resource, form: ResourceForm | None = None
) -> dict:
    if form is None:
        form = ResourceForm(
            instance=Revision(**revision_values(resource.draft)),
            user=request.user,
            initial={"version": resource.version},
        )
    return {
        "resource": resource,
        "form": form,
        "publisher": can_publish(request.user),
        "schedule_form": ScheduleForm(),
        "history": Paginator(resource.revisions.select_related("author"), 20).get_page(
            request.GET.get("history_page")
        ),
        "events": resource.events.select_related("actor")[:15],
    }


@editor_required
@require_http_methods(["GET", "POST"])
def edit(request: HttpRequest, pk: UUID) -> HttpResponse:
    resource = get_object_or_404(scoped_resources(request.user), pk=pk)
    if request.method == "GET":
        return render(request, "resources/edit.html", editor_context(request, resource))
    autosave = request.POST.get("autosave") == "1"
    form = None
    try:
        with transaction.atomic():
            resource = get_object_or_404(
                scoped_resources(request.user).select_for_update(of=("self",)), pk=pk
            )
            check_version(resource, request.POST.get("version"))
            form = ResourceForm(
                request.POST,
                request.FILES,
                user=request.user,
                instance=Revision(**revision_values(resource.draft)),
            )
            if not form.is_valid():
                if autosave:
                    return JsonResponse({"errors": form.errors}, status=400)
                return render(
                    request,
                    "resources/edit.html",
                    editor_context(request, resource, form),
                    status=400,
                )
            save_draft(resource, form, request.user)
        if autosave:
            return JsonResponse(
                {
                    "version": resource.version,
                    "saved_at": timezone.now().isoformat(),
                    "title": resource.draft.title,
                    "asset_id": str(resource.draft.asset_id or ""),
                    "asset_name": resource.draft.asset.name
                    if resource.draft.asset_id
                    else "",
                    "cover_id": str(resource.draft.cover_id or ""),
                }
            )
        messages.success(request, "Draft saved.")
        return redirect("resources:edit", pk=pk)
    except ValidationError as exc:
        status = 409 if isinstance(exc, EditConflict) else 400
        if autosave:
            return JsonResponse({"errors": exc.messages}, status=status)
        if form is None:
            form = ResourceForm(
                request.POST,
                request.FILES,
                user=request.user,
                instance=Revision(**revision_values(resource.draft)),
            )
            form.is_valid()
        form.add_error(None, exc)
        return render(
            request,
            "resources/edit.html",
            editor_context(request, resource, form),
            status=status,
        )


@editor_required
@require_POST
def action(request: HttpRequest, pk: UUID) -> HttpResponse:
    try:
        with transaction.atomic():
            resource = get_object_or_404(
                scoped_resources(request.user).select_for_update(of=("self",)), pk=pk
            )
            check_version(resource, request.POST.get("version"))
            operation = request.POST.get("action")
            if operation in {
                "publish",
                "schedule",
                "cancel_schedule",
                "archive",
                "restore",
            } and not can_publish(request.user):
                raise PermissionDenied
            if operation in {"publish", "schedule"}:
                at = None
                if operation == "schedule":
                    form = ScheduleForm(request.POST)
                    if not form.is_valid():
                        raise ValidationError(
                            [
                                str(error)
                                for errors in form.errors.values()
                                for error in errors
                            ]
                        )
                    at = form.cleaned_data["publish_at"]
                publish(resource, request.user, at=at)
                messages.success(
                    request,
                    "Publication scheduled (UTC)."
                    if at
                    else "Published — your resource is now available.",
                )
            elif operation == "submit":
                if resource.archived:
                    raise ValidationError("Restore this resource before submitting it.")
                validate_publication(resource.draft)
                resource.submitted = True
                record_change(resource, request.user, "submitted")
                messages.success(
                    request,
                    "Submitted for review. A publisher can find it in the In review tab.",
                )
            elif operation == "duplicate":
                new = duplicate(resource, request.user)
                record_change(resource, request.user, "duplicated")
                messages.success(request, "Created an unpublished copy.")
                return redirect("resources:edit", pk=new.pk)
            elif operation in {"archive", "restore"}:
                resource.archived = operation == "archive"
                record_change(resource, request.user, operation)
                messages.success(
                    request,
                    "Archived and hidden from visitors."
                    if resource.archived
                    else "Restored. Previous publication settings apply again.",
                )
            elif operation == "cancel_schedule":
                resource.live = resource.published_revision
                resource.scheduled = None
                resource.publish_at = None
                record_change(resource, request.user, operation)
                messages.success(
                    request,
                    "Schedule cancelled; the current published version is unchanged.",
                )
            elif operation == "restore_revision":
                revision_id = request.POST.get("revision", "")
                if not revision_id.isdigit() or len(revision_id) > 18:
                    raise ValidationError("Choose a valid version to restore.")
                revision = get_object_or_404(resource.revisions, pk=revision_id)
                resource.draft = Revision.objects.create(
                    resource=resource, author=request.user, **revision_values(revision)
                )
                resource.submitted = False
                record_change(resource, request.user, operation)
                messages.success(
                    request, "Version restored to draft. Review and publish when ready."
                )
            else:
                raise ValidationError("Choose a valid resource action.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("resources:edit", pk=pk)


def revision_context(revision: Revision, *, preview: bool = False) -> dict:
    route = "resources:preview_asset" if preview else "resources:asset"
    asset_url = reverse(route, args=[revision.resource_id, "file"])
    cover_url = reverse(route, args=[revision.resource_id, "cover"])
    if preview:
        asset_url += f"?revision={revision.pk}"
        cover_url += f"?revision={revision.pk}"
    return {
        "revision": revision,
        "resource": revision.resource,
        "preview": preview,
        "asset_url": asset_url,
        "cover_url": cover_url,
        "embed_url": video_embed(revision.url),
    }


@editor_required
@require_GET
@xframe_options_sameorigin
def preview(request: HttpRequest, pk: UUID) -> HttpResponse:
    resource = get_object_or_404(scoped_resources(request.user), pk=pk)
    revision_id = request.GET.get("revision")
    revision = (
        get_object_or_404(resource.revisions, pk=revision_id)
        if revision_id and revision_id.isdigit()
        else resource.draft
    )
    return render(
        request, "resources/detail.html", revision_context(revision, preview=True)
    )


@public_site
@require_GET
def detail(request: HttpRequest, pk: UUID, slug: str) -> HttpResponse:
    revision = get_object_or_404(
        published_revisions(), resource_id=pk, resource__slug=slug
    )
    if revision.access == Revision.Access.FAMILY and not request.session.get(
        FAMILY_RESOURCES_SESSION_KEY
    ):
        request.session["family_resource_return_to"] = (
            revision.resource.get_absolute_url()
        )
        return redirect("marketing_resources")
    return render(request, "resources/detail.html", revision_context(revision))


def asset_response(revision: Revision, role: str, request: HttpRequest) -> HttpResponse:
    if role not in {"file", "cover"}:
        raise Http404
    asset = revision.asset if role == "file" else revision.image_asset
    if asset is None:
        raise Http404
    if request.GET.get("thumbnail") == "1":
        if not asset.preview_id:
            raise Http404
        asset = asset.preview
    inline = (
        role == "cover"
        or request.GET.get("thumbnail") == "1"
        or (
            request.GET.get("inline") == "1"
            and (asset.content_type == "application/pdf" or asset.is_image)
        )
    )
    response = FileResponse(
        BytesIO(bytes(asset.data)),
        content_type=asset.content_type,
        as_attachment=not inline,
        filename=asset.name,
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@public_site
@require_GET
@xframe_options_sameorigin
def asset(request: HttpRequest, pk: UUID, role: str) -> HttpResponse:
    revision = get_object_or_404(published_revisions(), resource_id=pk)
    if revision.access == Revision.Access.FAMILY and not request.session.get(
        FAMILY_RESOURCES_SESSION_KEY
    ):
        request.session["family_resource_return_to"] = (
            revision.resource.get_absolute_url()
        )
        return redirect("marketing_resources")
    return asset_response(revision, role, request)


@editor_required
@require_GET
@xframe_options_sameorigin
def preview_asset(request: HttpRequest, pk: UUID, role: str) -> HttpResponse:
    resource = get_object_or_404(scoped_resources(request.user), pk=pk)
    revision_id = request.GET.get("revision", "")
    revision = (
        get_object_or_404(resource.revisions, pk=revision_id)
        if revision_id.isdigit()
        else resource.draft
    )
    return asset_response(revision, role, request)


@editor_required
@require_GET
def media_library(request: HttpRequest) -> HttpResponse:
    assets = (
        scoped_assets(request.user)
        .annotate(uses=Count("file_revisions__resource", distinct=True))
        .order_by("-created_at", "pk")
    )
    query = request.GET.get("q", "").strip()[:200]
    if query:
        assets = assets.filter(name__icontains=query)
    return render(
        request,
        "resources/media.html",
        {
            "page": Paginator(assets, 30).get_page(request.GET.get("page")),
            "query": query,
        },
    )
