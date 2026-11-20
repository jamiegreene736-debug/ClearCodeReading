from datetime import datetime, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.meetings.access import teacher_families
from apps.meetings.models import FamilyMeeting, MeetingNotice
from apps.users.models import CustomUser


def queue_notices(meeting: FamilyMeeting, kind: MeetingNotice.Kind) -> None:
    now = timezone.now()
    meeting.notices.filter(state=MeetingNotice.State.PENDING).update(
        state=MeetingNotice.State.SKIPPED
    )
    meeting.notices.create(kind=kind, revision=meeting.revision, due_at=now)
    reminder_at = meeting.starts_at - timedelta(days=1)
    if not meeting.cancelled and reminder_at > now:
        meeting.notices.create(
            kind=MeetingNotice.Kind.REMINDER,
            revision=meeting.revision,
            due_at=reminder_at,
        )


@transaction.atomic
def save_meeting(
    teacher: CustomUser,
    family_id: int,
    starts_at: datetime,
    ends_at: datetime,
    location: str,
    *,
    meeting_id: int | None = None,
    revision: int | None = None,
    cancel: bool = False,
) -> FamilyMeeting:
    if (
        teacher.role != CustomUser.Role.TEACHER
        or not teacher.is_active
        or teacher.is_deleted
    ):
        raise PermissionDenied
    family = teacher_families(teacher).filter(pk=family_id).first()
    if family is None:
        raise PermissionDenied
    # Both calendars must be locked even if two teachers invite the same parent.
    list(
        CustomUser.objects.select_for_update()
        .filter(pk__in=[teacher.pk, family.guardian_id])
        .order_by("pk")
    )
    if (
        not teacher_families(teacher)
        .select_for_update()
        .filter(pk=family_id, child_id=family.child_id, guardian_id=family.guardian_id)
        .exists()
    ):
        raise PermissionDenied
    if meeting_id is None:
        if cancel:
            raise ValidationError("Choose an existing meeting to cancel.")
        meeting = FamilyMeeting(
            teacher=teacher,
            parent=family.guardian,
            child=family.child,
            school_id=family.child.school_id,
        )
        kind = MeetingNotice.Kind.INVITE
    else:
        meeting = FamilyMeeting.objects.select_for_update().get(
            pk=meeting_id, teacher=teacher, parent=family.guardian, child=family.child
        )
        if (
            meeting.revision != revision
            or meeting.cancelled
            or meeting.starts_at <= timezone.now()
        ):
            raise ValidationError(
                "This meeting has changed or started. Open the schedule to see its current details."
            )
        if not cancel and (meeting.starts_at, meeting.ends_at, meeting.location) == (
            starts_at,
            ends_at,
            location,
        ):
            raise ValidationError("The time and location are unchanged.")
        meeting.revision += 1
        kind = MeetingNotice.Kind.CANCEL if cancel else MeetingNotice.Kind.UPDATE

    if cancel:
        meeting.cancelled = True
    else:
        if starts_at <= timezone.now() or ends_at <= starts_at:
            raise ValidationError("Choose a future start and an end after the start.")
        overlaps = FamilyMeeting.objects.filter(
            Q(teacher=teacher) | Q(parent_id=family.guardian_id),
            cancelled=False,
            starts_at__lt=ends_at,
            ends_at__gt=starts_at,
        ).exclude(pk=meeting.pk)
        if overlaps.exists():
            raise ValidationError(
                "The teacher or parent already has a family meeting during that time."
            )
        meeting.starts_at, meeting.ends_at, meeting.location = (
            starts_at,
            ends_at,
            location,
        )
    meeting.full_clean()
    meeting.save()
    queue_notices(meeting, kind)
    return meeting
