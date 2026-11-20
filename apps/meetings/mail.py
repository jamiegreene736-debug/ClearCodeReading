"""Private meeting delivery through the portal invitation mailbox."""

import base64
import logging
from datetime import timedelta
from email.message import EmailMessage

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.crm_email.google import Gmail, ProviderError
from apps.crm_email.models import BETHANY_EMAIL, Mailbox
from apps.crm_email.security import EmailError, mailbox_lock
from apps.crm_email.services import active_mailbox
from apps.crm_email.sync import provider_id
from apps.crm_email.worker import backoff
from apps.meetings.access import teacher_families
from apps.meetings.models import FamilyMeeting, MeetingNotice

logger = logging.getLogger(__name__)
State = MeetingNotice.State


def message_id(notice: MeetingNotice) -> str:
    return f"<family-meeting-{notice.pk}@{settings.CRM_EMAIL_DOMAIN}>"


def send_notice(client: Gmail, notice: MeetingNotice) -> None:
    meeting = notice.meeting
    local_start = timezone.localtime(meeting.starts_at)
    local_end = timezone.localtime(meeting.ends_at)
    validate_email(meeting.parent.email)
    message = EmailMessage()
    message["Message-ID"] = message_id(notice)
    message["From"] = BETHANY_EMAIL
    message["To"] = meeting.parent.email
    message["Subject"] = (
        f"Family meeting {notice.get_kind_display().lower()} | ClearCode Reading"
    )
    intro = {
        MeetingNotice.Kind.INVITE: "You are invited to a family meeting.",
        MeetingNotice.Kind.UPDATE: "Your family meeting details have changed.",
        MeetingNotice.Kind.REMINDER: "This is a reminder about your upcoming family meeting.",
        MeetingNotice.Kind.CANCEL: "Your family meeting has been cancelled.",
    }[MeetingNotice.Kind(notice.kind)]
    message.set_content(
        f"Hello {meeting.parent},\n\n{intro}\n\n"
        f"Reader: {meeting.child}\nTeacher: {meeting.teacher}\n"
        f"Start: {local_start:%A, %B %d, %Y at %I:%M %p %Z}\n"
        f"End: {local_end:%A, %B %d, %Y at %I:%M %p %Z}\n"
        f"Meeting location: {meeting.location}\n\n"
        f"View your schedule: {settings.PUBLIC_APP_URL.rstrip('/')}{reverse('family_meetings')}\n"
    )
    result = client.request(
        "POST",
        "messages/send",
        json={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()},
    )
    try:
        provider_id(result.get("id"))
    except EmailError as exc:
        # The POST may already have succeeded even when its receipt is malformed.
        raise ProviderError() from exc


def claim(notice_id: int) -> MeetingNotice | None:
    # Edits and dispatch lock the meeting before touching notices. Commit the
    # checking state before external I/O so a worker restart reconciles first.
    with transaction.atomic():
        meeting_id = MeetingNotice.objects.values_list("meeting_id", flat=True).get(
            pk=notice_id
        )
        FamilyMeeting.objects.select_for_update().get(pk=meeting_id)
        notice = (
            MeetingNotice.objects.select_for_update(of=("self",))
            .select_related("meeting__teacher", "meeting__child", "meeting__parent")
            .get(pk=notice_id)
        )
        if notice.state != State.PENDING:
            return None
        meeting = notice.meeting
        valid = (
            notice.revision == meeting.revision
            and meeting.parent.can_add_child
            and meeting.teacher.is_family_teacher
            and meeting.teacher.is_active
            and not meeting.teacher.is_deleted
            and teacher_families(meeting.teacher)
            .filter(child_id=meeting.child_id, guardian_id=meeting.parent_id)
            .exists()
            and (
                meeting.cancelled
                if notice.kind == MeetingNotice.Kind.CANCEL
                else not meeting.cancelled and meeting.starts_at > timezone.now()
            )
        )
        if not valid:
            notice.state = State.SKIPPED
            notice.save(update_fields=["state"])
            return None
        notice.state = State.CHECKING
        notice.attempts += 1
        notice.due_at = timezone.now() + timedelta(minutes=1)
        notice.save(update_fields=["state", "attempts", "due_at"])
        return notice


def defer(notice: MeetingNotice, state: str) -> None:
    notice.state = State.REVIEW if notice.attempts >= 8 else state
    notice.due_at = timezone.now() + backoff(notice.attempts)
    notice.save(update_fields=["state", "due_at", "attempts"])
    if notice.state == State.REVIEW:
        logger.warning("family_meeting_delivery_review notice_id=%s", notice.pk)


def process_notice(client: Gmail, notice: MeetingNotice) -> None:
    if notice.state == State.CHECKING:
        notice.attempts += 1
        try:
            result = client.request(
                "GET",
                "messages",
                params={
                    "q": f"in:sent rfc822msgid:{message_id(notice)}",
                    "maxResults": 2,
                },
            )
            matches = result.get("messages")
            if (
                not isinstance(matches, list)
                or len(matches) != 1
                or not isinstance(matches[0], dict)
            ):
                defer(notice, State.CHECKING)
                return
            provider_id(matches[0].get("id"))
        except EmailError:
            defer(notice, State.CHECKING)
            return
    else:
        claimed = claim(notice.pk)
        if claimed is None:
            return
        notice = claimed
        try:
            send_notice(client, notice)
        except ProviderError as exc:
            if exc.status in {403, 429} and exc.retryable:
                defer(notice, State.PENDING)
            elif 400 <= exc.status < 500:
                defer(notice, State.REVIEW)
            else:
                defer(notice, State.CHECKING)
            return
        except ValidationError:
            defer(notice, State.REVIEW)
            return
        except EmailError:
            # Gmail rejected its setup or token before submitting the message.
            defer(notice, State.PENDING)
            return

    notice.state = State.SENT
    notice.sent_at = timezone.now()
    notice.save(update_fields=["state", "sent_at", "attempts"])


def process_due_notices() -> None:
    # The email worker holds its global advisory lock for the entire pass.
    due = MeetingNotice.objects.filter(
        state__in=[State.PENDING, State.CHECKING], due_at__lte=timezone.now()
    )
    batch = list(due.order_by("due_at", "pk")[:5])
    if not batch:
        return
    mailbox = (
        Mailbox.objects.select_related("user")
        .filter(email__iexact=BETHANY_EMAIL, user__email__iexact=BETHANY_EMAIL)
        .first()
    )
    try:
        if mailbox is None:
            raise EmailError("Meeting mailbox unavailable.")
        with mailbox_lock(mailbox.pk):
            client = Gmail(active_mailbox(mailbox.user))
            for notice in batch:
                process_notice(client, notice)
    except (EmailError, PermissionDenied):
        # No send was attempted without the connected mailbox. Back off the
        # affected rows, retaining any state changed by a simultaneous edit.
        for notice in batch:
            due.filter(pk=notice.pk, state=notice.state).update(
                due_at=timezone.now() + timedelta(minutes=5)
            )
        logger.warning("family_meeting_mailbox_unavailable")
