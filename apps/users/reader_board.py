"""Private notes and in-app notifications shared by a reader's family and assigned teacher."""

from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from apps.api.permissions import models_consent_filter
from apps.users.models import (
    BoardTextAccount,
    ChildProfile,
    CustomUser,
    GuardianRelationship,
    ReaderBoardNotification,
    ReaderBoardPost,
)
from apps.users.onboarding_views import PortalRequest


def active_families() -> QuerySet[GuardianRelationship]:
    return GuardianRelationship.objects.filter(
        is_deleted=False,
        guardian__role=CustomUser.Role.GUARDIAN,
        guardian__is_active=True,
        guardian__is_deleted=False,
        child__is_deleted=False,
        consent_status=GuardianRelationship.ConsentStatus.GRANTED,
    ).filter(models_consent_filter())


def board_readers(user: CustomUser) -> QuerySet[ChildProfile]:
    readers = ChildProfile.objects.filter(is_deleted=False)
    if not user.is_active or user.is_deleted:
        return readers.none()
    if user.can_add_child:
        return readers.filter(
            pk__in=active_families().filter(guardian=user).values("child_id")
        )
    if user.role == CustomUser.Role.TEACHER:
        return readers.filter(
            Q(learning_profile__assigned_teacher_id=user.pk)
            | Q(learning_profile__assigned_teacher_id=str(user.pk))
        )
    return readers.none()


def assigned_teacher(child: ChildProfile) -> CustomUser | None:
    teacher_id = (child.learning_profile or {}).get("assigned_teacher_id")
    if (
        not str(teacher_id).isascii()
        or not str(teacher_id).isdigit()
        or len(str(teacher_id)) > 18
    ):
        return None
    return CustomUser.objects.filter(
        pk=int(str(teacher_id)),
        role=CustomUser.Role.TEACHER,
        is_active=True,
        is_deleted=False,
    ).first()


def unread_counts(user: CustomUser, readers: QuerySet[ChildProfile]) -> dict[int, int]:
    counts = (
        ReaderBoardNotification.objects.filter(
            recipient=user, read_at__isnull=True, post__child__in=readers
        )
        .values("post__child_id")
        .annotate(total=Count("pk"))
    )
    return {row["post__child_id"]: row["total"] for row in counts}


class BoardNoteForm(forms.Form):
    submission_id = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput)
    body = forms.CharField(
        label="Your note", max_length=2000, widget=forms.Textarea(attrs={"rows": 3})
    )
    resource_url = forms.URLField(
        label="Resource link for this week (optional)",
        max_length=1000,
        required=False,
        assume_scheme="https",
    )

    def clean_resource_url(self) -> str:
        url = str(self.cleaned_data.get("resource_url", ""))
        if url and urlsplit(url).scheme != "https":
            raise forms.ValidationError("Enter an HTTPS resource link.")
        return url


@transaction.atomic
def post_note(user: CustomUser, child_id: int, form: BoardNoteForm) -> None:
    child = get_object_or_404(board_readers(user).select_for_update(), pk=child_id)
    if user.role == CustomUser.Role.TEACHER:
        recipients = list(
            active_families().filter(child=child).values_list("guardian_id", flat=True)
        )
    else:
        teacher = assigned_teacher(child)
        recipients = [teacher.pk] if teacher else []
    if not recipients:
        form.add_error(
            None,
            "Posting opens when this reader has an assigned teacher and a participating family.",
        )
        return
    data = form.cleaned_data
    post, created = ReaderBoardPost.objects.get_or_create(
        child=child,
        author=user,
        submission_id=data["submission_id"],
        defaults={"body": data["body"], "resource_url": data.get("resource_url", "")},
    )
    if created:
        notices = ReaderBoardNotification.objects.bulk_create(
            [
                ReaderBoardNotification(post=post, recipient_id=recipient)
                for recipient in recipients
            ]
        )

        from apps.notifications.board_sms import queue_texts

        queue_texts(post, notices)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def reader_board(request: PortalRequest, child_id: int) -> HttpResponse:
    user = request.user
    child = get_object_or_404(board_readers(user), pk=child_id)
    form = BoardNoteForm(request.POST if request.method == "POST" else None)
    if user.role != CustomUser.Role.TEACHER:
        form.fields.pop("resource_url")
    if form.is_bound and form.is_valid():
        post_note(user, child_id, form)
        if not form.errors:
            messages.success(request, "Your note is on the board.")
            return redirect("portal_reader_board", child_id=child_id)
    notes = Paginator(
        ReaderBoardPost.objects.filter(child=child)
        .select_related("author")
        .prefetch_related("notifications__text_deliveries"),
        20,
    ).get_page(request.GET.get("page"))
    if request.method == "GET":
        displayed = [note.pk for note in notes]
        ReaderBoardNotification.objects.filter(
            recipient=user, post_id__in=displayed, read_at__isnull=True
        ).update(read_at=timezone.now())
    can_post = (
        active_families().filter(child=child).exists()
        if user.role == CustomUser.Role.TEACHER
        else assigned_teacher(child) is not None
    )
    account = BoardTextAccount.objects.filter(user=user).first()
    pending_code = request.session.get("reader_sms_join", "")
    if (
        not account
        or not account.code_expires_at
        or account.code_expires_at <= timezone.now()
    ):
        pending_code = ""
        request.session.pop("reader_sms_join", None)
    return render(
        request,
        "portal/reader_board.html",
        {
            "child": child,
            "notes": notes,
            "form": form,
            "can_post": can_post,
            "texting_configured": bool(
                settings.QUO_API_KEY and settings.QUO_WEBHOOK_KEY
            ),
            "texting_active": bool(account and account.verified_at),
            "join_code": pending_code,
        },
    )


@login_required
@never_cache
@require_GET
def board_notifications(request: PortalRequest) -> JsonResponse:
    if not (request.user.can_add_child or request.user.role == CustomUser.Role.TEACHER):
        raise PermissionDenied
    return JsonResponse(
        {"unread": unread_counts(request.user, board_readers(request.user))}
    )


def board_dashboard_context(
    user: CustomUser, archive_page: str | None = None
) -> dict[str, Any]:
    from apps.users.weekly_resources import weekly_resources

    readers = board_readers(user)
    unread = unread_counts(user, readers)
    latest_notes = {
        post.child_id: post
        for post in ReaderBoardPost.objects.filter(
            child__in=readers, author__role=CustomUser.Role.TEACHER
        )
        .select_related("author")
        .order_by("child_id", "-created_at", "-pk")
        .distinct("child_id")
    }
    return {
        "board_rows": [
            {
                "child": child,
                "unread": unread.get(child.pk, 0),
                "teacher_note": latest_notes.get(child.pk),
            }
            for child in readers
        ],
        **(weekly_resources(user, readers, archive_page) if user.can_add_child else {}),
    }
