from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.meetings.access import (
    require_schedule_access,
    teacher_families,
    visible_meetings,
)
from apps.meetings.forms import MeetingForm
from apps.meetings.models import FamilyMeeting, MeetingNotice
from apps.meetings.services import save_meeting
from apps.users.onboarding_views import PortalRequest
from apps.users.portal_views import user_can_manage_instruction


@login_required
@never_cache
@require_GET
def schedule(request: PortalRequest) -> HttpResponse:
    user = request.user
    meetings = visible_meetings(user)
    history = request.GET.get("view") == "history"
    current = Q(cancelled=False, ends_at__gt=timezone.now())
    meetings = (
        meetings.exclude(current).order_by("-starts_at")
        if history
        else meetings.filter(current)
    )
    staff = user_can_manage_instruction(user) or user.is_family_teacher
    if staff:
        meetings = meetings.prefetch_related(
            Prefetch(
                "notices",
                queryset=MeetingNotice.objects.exclude(
                    state=MeetingNotice.State.SKIPPED
                ).order_by("-revision", "pk"),
                to_attr="delivery_history",
            )
        )
    return render(
        request,
        "portal/family_meetings.html",
        {
            "page": Paginator(meetings, 25).get_page(request.GET.get("page")),
            "history": history,
            "teacher": user.is_family_teacher,
            "staff": staff,
            "now": timezone.now(),
        },
    )


def owned_meeting(request: PortalRequest, pk: int) -> FamilyMeeting:
    return get_object_or_404(
        visible_meetings(request.user), pk=pk, teacher=request.user, cancelled=False
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def meeting_form(request: PortalRequest, pk: int | None = None) -> HttpResponse:
    require_schedule_access(request.user)
    if not request.user.is_family_teacher:
        raise PermissionDenied
    meeting = owned_meeting(request, pk) if pk is not None else None
    if meeting and meeting.starts_at <= timezone.now():
        raise Http404
    form = MeetingForm(
        request.user,
        request.POST if request.method == "POST" else None,
        meeting=meeting,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            save_meeting(
                request.user,
                data["family"].pk,
                data["starts_at"],
                data["ends_at"],
                data["location"],
                meeting_id=pk,
                revision=data["revision"],
            )
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            messages.success(request, "Meeting saved. The parent’s email is queued.")
            return redirect("family_meetings")
    return render(
        request,
        "portal/family_meeting_form.html",
        {
            "form": form,
            "meeting": meeting,
            "has_families": teacher_families(request.user).exists(),
        },
    )


@login_required
@never_cache
@require_POST
def cancel_meeting(request: PortalRequest, pk: int) -> HttpResponse:
    if not request.user.is_family_teacher:
        raise PermissionDenied
    meeting = owned_meeting(request, pk)
    try:
        revision = int(request.POST.get("revision", ""))
    except ValueError:
        revision = None
    try:
        family = get_object_or_404(
            teacher_families(request.user), child=meeting.child, guardian=meeting.parent
        )
        save_meeting(
            request.user,
            family.pk,
            meeting.starts_at,
            meeting.ends_at,
            meeting.location,
            meeting_id=pk,
            revision=revision,
            cancel=True,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(
            request, "Meeting cancelled. The parent’s cancellation email is queued."
        )
    return redirect("family_meetings")
