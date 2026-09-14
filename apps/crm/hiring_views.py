"""Private, server-rendered hiring workspace and guarded mutations."""

from io import BytesIO
from typing import Any, cast
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, IntegerField, Q, QuerySet, Value, When
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.core.models import RecruitingInterest
from apps.crm.access import crm_owner_queryset
from apps.crm.hiring import (
    ACTIVE_STAGES,
    CHECKLISTS,
    DEFAULT_ACTIONS,
    business_due_date,
    hiring_owner_queryset,
)
from apps.crm.hiring_forms import HiringOwnerForm, HiringUpdateForm
from apps.crm.hiring_models import HiringCandidate, HiringEvent
from apps.crm.models import Lead
from apps.crm.views import CrmAccessMixin
from apps.users.models import AuditLog, CustomUser


class HiringAccessMixin(CrmAccessMixin):
    request: HttpRequest

    def test_func(self) -> bool:
        return cast(CustomUser, self.request.user).has_hiring_access

    def dispatch(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


def valid_record_id(value: str) -> bool:
    return bool(
        value
        and value.isascii()
        and value.isdecimal()
        and len(value) <= 19
        and 0 < int(value) <= 9223372036854775807
    )


def candidate_queryset() -> QuerySet[HiringCandidate]:
    return (
        HiringCandidate.objects.filter(application__career_path="teacher")
        .select_related("application", "application__owner", "lead")
        .defer("application__resume_data", "application__cover_letter_data")
    )


def candidate_url(candidate: HiringCandidate) -> str:
    return (
        reverse("crm_hiring")
        + "?"
        + urlencode({"owner": "all", "candidate": candidate.pk})
        + "#candidate-record"
    )


def render_workspace(
    request: HttpRequest,
    *,
    candidate: HiringCandidate | None = None,
    form: HiringUpdateForm | None = None,
    owner_form: HiringOwnerForm | None = None,
    status: int = 200,
) -> HttpResponse:
    owner = request.GET.get("owner", "mine")
    attention = request.GET.get("attention") == "1"
    stage = request.GET.get("stage", "active")
    query = request.GET.get("q", "").strip()[:255]
    candidates = candidate_queryset()
    eligible = hiring_owner_queryset()
    attention_query = (
        Q(due_date__lt=timezone.localdate())
        | Q(due_date__isnull=True)
        | ~Q(application__owner_id__in=eligible.values("pk"))
        | ~Q(blocker="")
    )
    if owner == "mine":
        candidates = candidates.filter(application__owner_id=request.user.pk)
    elif owner == "unassigned":
        candidates = candidates.exclude(application__owner_id__in=eligible.values("pk"))
    elif valid_record_id(owner):
        candidates = candidates.filter(application__owner_id=int(owner))
    else:
        owner = "all"
    if stage in HiringCandidate.Stage.values:
        candidates = candidates.filter(stage=stage)
    elif stage != "all":
        stage = "active"
        candidates = candidates.exclude(
            stage__in=["ready", "not_selected", "withdrawn"]
        )
    if attention:
        candidates = candidates.exclude(
            stage__in=["ready", "not_selected", "withdrawn"]
        ).filter(attention_query)
    if query:
        candidates = candidates.filter(
            Q(application__name__icontains=query)
            | Q(application__email__icontains=query)
        )
    candidates = candidates.annotate(
        attention_rank=Case(
            When(attention_query, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("attention_rank", "due_date", "created_at", "pk")
    page = Paginator(candidates, 25).get_page(request.GET.get("page"))
    if candidate is None:
        selected = request.GET.get("candidate", "")
        if selected:
            if not valid_record_id(selected):
                raise Http404("Invalid candidate.")
            candidate = get_object_or_404(candidate_queryset(), pk=selected)
        else:
            candidate = next(iter(page), None)
    filters = {
        "owner": owner,
        "stage": stage,
        "q": query,
        "attention": "1" if attention else "",
    }
    for item in page:
        item.selection_url = (
            reverse("crm_hiring")
            + "?"
            + urlencode(filters | {"page": page.number, "candidate": item.pk})
            + "#candidate-record"
        )
    if candidate:
        form = form if form is not None else HiringUpdateForm(instance=candidate)
        owner_form = (
            owner_form
            if owner_form is not None
            else HiringOwnerForm(
                initial={
                    "owner": candidate.application.owner_id,
                    "revision": candidate.revision,
                }
            )
        )
    context = {
        "page_obj": page,
        "candidate": candidate,
        "form": form,
        "owner_form": owner_form,
        "owners": eligible,
        "owner_filter": owner,
        "stage_filter": stage,
        "attention": attention,
        "query": query,
        "action_defaults": DEFAULT_ACTIONS,
        "suggested_due_date": business_due_date().isoformat(),
        "stages": HiringCandidate.Stage.choices,
        "journey": [(s, dict(HiringCandidate.Stage.choices)[s]) for s in ACTIVE_STAGES],
        "checklist_groups": [
            (dict(HiringCandidate.Stage.choices)[s], items)
            for s, items in CHECKLISTS.items()
        ],
        "events": candidate.events.select_related("actor")[:20] if candidate else [],
        "can_edit": bool(
            candidate and candidate.application.owner_id == request.user.pk
        ),
        "filter_query": urlencode(filters),
        "unassigned_count": candidate_queryset()
        .exclude(stage__in=["ready", "not_selected", "withdrawn"])
        .exclude(application__owner_id__in=eligible.values("pk"))
        .count(),
    }
    return render(request, "crm/hiring.html", context, status=status)


class HiringWorkspaceView(HiringAccessMixin, View):
    def get(self, request: HttpRequest) -> HttpResponse:
        return render_workspace(request)


class HiringUpdateView(HiringAccessMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        with transaction.atomic():
            candidate = get_object_or_404(
                candidate_queryset().select_for_update(of=("self", "application")),
                pk=pk,
            )
            if candidate.application.owner_id != request.user.pk:
                raise PermissionDenied(
                    "Only the assigned owner can update the hiring record. Reassign ownership first."
                )
            before = {
                field: getattr(candidate, field)
                for field in HiringUpdateForm.Meta.fields
            }
            data = request.POST.copy()
            if (
                data.get("stage") != candidate.stage
                and data.get("stage") in HiringCandidate.Stage.values
            ):
                if data.get("next_action", "") == candidate.next_action:
                    data["next_action"] = DEFAULT_ACTIONS.get(str(data["stage"]), "")
                if data.get("due_date", "") == str(candidate.due_date or ""):
                    data["due_date"] = (
                        business_due_date().isoformat()
                        if data["stage"] in DEFAULT_ACTIONS
                        else ""
                    )
            form = HiringUpdateForm(data, instance=candidate)
            valid = form.is_valid()
            if request.POST.get("revision") != str(before["revision"]):
                form.add_error(
                    None,
                    "This record changed in another session. Reload the candidate before saving again.",
                )
                valid = False
            if not valid:
                return render_workspace(
                    request, candidate=candidate, form=form, status=400
                )
            changed = [
                field
                for field in HiringUpdateForm.Meta.fields
                if field != "revision" and before[field] != getattr(candidate, field)
            ]
            if not changed:
                messages.info(request, "No changes to save.")
                return redirect(candidate_url(candidate))
            if "stage" in changed:
                candidate.stage_entered_at = timezone.now()
            candidate.revision = before["revision"] + 1
            candidate.save()
            candidate.application.status = (
                "closed" if candidate.is_terminal else "reviewing"
            )
            candidate.application.save(update_fields=["status", "updated_at"])
            summary = (
                f"Moved to {candidate.get_stage_display()}."
                if "stage" in changed
                else "Hiring record updated."
            )
            HiringEvent.objects.create(
                candidate=candidate,
                actor_id=request.user.pk,
                summary=summary,
                changes={
                    field: {"before": before[field], "after": getattr(candidate, field)}
                    for field in changed
                    if field in {"stage", "decision", "offer_response"}
                }
                | {"fields": changed},
            )
        messages.success(request, "Hiring record saved. You own the next step.")
        return redirect(candidate_url(candidate))


class HiringAssignView(HiringAccessMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        with transaction.atomic():
            candidate = get_object_or_404(
                candidate_queryset().select_for_update(of=("self", "application")),
                pk=pk,
            )
            form = HiringOwnerForm(request.POST)
            valid = form.is_valid()
            if request.POST.get("revision") != str(candidate.revision):
                form.add_error(None, "This record changed. Reload before reassigning.")
                valid = False
            if not valid:
                return render_workspace(
                    request, candidate=candidate, owner_form=form, status=400
                )
            owner = form.cleaned_data["owner"]
            previous = candidate.application.owner
            if previous == owner:
                return redirect(candidate_url(candidate))
            candidate.application.owner = owner
            candidate.application.save(update_fields=["owner", "updated_at"])
            candidate.revision += 1
            candidate.save(update_fields=["revision", "updated_at"])
            HiringEvent.objects.create(
                candidate=candidate,
                actor_id=request.user.pk,
                summary=f"Full ownership transferred from {str(previous or 'Unassigned')[:160]} to {str(owner)[:160]}.",
                changes={
                    "owner": {
                        "before": previous.pk if previous else None,
                        "after": owner.pk,
                    }
                },
            )
        messages.success(
            request, f"{owner} now owns this candidate from start to finish."
        )
        return redirect(candidate_url(candidate))


class HiringDocumentView(HiringAccessMixin, View):
    def get(self, request: HttpRequest, pk: int, kind: str) -> HttpResponseBase:
        candidate = get_object_or_404(candidate_queryset(), pk=pk)
        field = {"resume": "resume", "cover-letter": "cover_letter"}.get(kind)
        if field is None:
            raise Http404
        application = candidate.application
        data = getattr(application, field + "_data")
        legacy = getattr(application, field)
        if data:
            document = BytesIO(bytes(data))
        elif legacy:
            try:
                document = legacy.open("rb")
            except FileNotFoundError as exc:
                raise Http404("Document no longer available.") from exc
        else:
            raise Http404("No document uploaded.")
        response = FileResponse(
            document,
            as_attachment=True,
            filename=getattr(application, field + "_original_name") or legacy.name,
            content_type="application/octet-stream",
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response


class HiringAccessUpdateView(CrmAccessMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        if not cast(CustomUser, request.user).can_manage_crm_users:
            raise PermissionDenied
        member = get_object_or_404(crm_owner_queryset(), pk=pk)
        member.hiring_enabled = request.POST.get("enabled") == "1"
        member.save(update_fields=["hiring_enabled", "updated_at"])
        AuditLog.objects.create(
            actor_id=request.user.pk,
            action="crm.hiring_access.updated",
            entity_type="CustomUser",
            entity_id=str(member.pk),
            after={"hiring_enabled": member.hiring_enabled},
        )
        messages.success(request, f"Hiring access updated for {member}.")
        return redirect("crm_team")


class HiringFromContactView(HiringAccessMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        with transaction.atomic():
            lead = get_object_or_404(
                Lead.objects.select_for_update(), pk=pk, is_deleted=False
            )
            existing = HiringCandidate.objects.filter(lead=lead).first()
            if existing:
                return redirect(candidate_url(existing))
            existing = (
                candidate_queryset()
                .filter(application__email__iexact=lead.contact_email)
                .first()
            )
            if existing:
                if existing.lead_id is None:
                    existing.lead = lead
                    existing.save(update_fields=["lead", "updated_at"])
                messages.info(
                    request, "Opened the existing application for this email."
                )
                return redirect(candidate_url(existing))
            application = RecruitingInterest.objects.create(
                name=lead.contact_name,
                email=lead.contact_email,
                phone=lead.contact_phone,
                career_path="teacher",
                role_interest="Teaching or reading specialist",
                notes="Added from CRM contact after hiring interest was confirmed.",
                source_path=f"/crm/contacts/{lead.pk}/",
                owner_id=request.user.pk,
            )
            candidate = application.hiring
            candidate.lead = lead
            candidate.due_date = business_due_date()
            candidate.save(update_fields=["lead", "due_date", "updated_at"])
            HiringEvent.objects.create(
                candidate=candidate,
                actor_id=request.user.pk,
                summary="Hiring interest confirmed; linked to existing CRM contact.",
            )
        return redirect(candidate_url(candidate))
