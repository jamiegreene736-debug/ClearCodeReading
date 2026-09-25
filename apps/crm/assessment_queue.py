"""Queue helpers for parent reading inventories.

Staff work assessments by the next action: who still has to finish, who
needs a review, and which emails failed. People are counted separately
from invitations because one family can have more than one child.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Min, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.crm.inventory import InventoryError, definition
from apps.crm.inventory_models import InventoryInvitation
from apps.crm.routing import waiting_label


QUEUE_CHOICES = (
    ("waiting", "Waiting to finish"),
    ("not_started", "Not started"),
    ("in_progress", "In progress"),
    ("review", "Needs review"),
    ("attention", "Email needs attention"),
    ("booked", "Consultation booked"),
    ("finished", "Finished"),
    ("all", "All"),
)
QUEUE_KEYS = {key for key, _label in QUEUE_CHOICES}
LEGACY_STATUS = {
    "pending": "waiting",
    "completed": "finished",
    "review": "review",
    "booked": "booked",
    "failed": "attention",
}
QUEUE_INTRO = {
    "waiting": "These people have an open inventory and have not submitted it. Oldest invitations are first.",
    "not_started": "The invitation exists, and the parent has not opened it yet.",
    "in_progress": "The parent started and still has sections left. A reminder is more useful than a new invitation.",
    "review": "Finished inventories waiting for a person to read the answers and mark them reviewed.",
    "attention": "The latest email failed or delivery is uncertain. The invitation is saved; retry from the assessment.",
    "booked": "A consultation is already on the calendar from this inventory.",
    "finished": "Submitted inventories, including ones that still need review.",
    "all": "Every inventory, including finished, expired, and withdrawn links.",
}
OUTCOME_LABELS = {
    "support": "Next step: a reading-support conversation",
    "resources": "Next step: home reading resources",
    "review": "Score is on the cutoff. Decide the recommendation before following up.",
}


def visible_invitations():
    return InventoryInvitation.objects.filter(child__parent__is_deleted=False)


def open_filter(now=None):
    now = now or timezone.now()
    return Q(completed_at__isnull=True, revoked_at__isnull=True, expires_at__gt=now)


def pending_completion_people_count(now=None) -> int:
    """Distinct parents who still need to finish at least one open inventory."""
    return (
        visible_invitations()
        .filter(open_filter(now))
        .values("child__parent_id")
        .distinct()
        .count()
    )


def selected_queue(params) -> str:
    queue = (params.get("queue") or "").strip()
    if queue in QUEUE_KEYS:
        return queue
    if "status" in params:
        status = params.get("status") or ""
        return LEGACY_STATUS.get(status, "all" if status == "" else "waiting")
    return "waiting"


def apply_search(queryset, query: str):
    query = " ".join((query or "").split())
    if not query:
        return queryset, ""
    return (
        queryset.filter(
            Q(child__name__icontains=query)
            | Q(child__parent__contact_name__icontains=query)
            | Q(recipient__icontains=query)
            | Q(child__parent__contact_email__icontains=query)
        ),
        query,
    )


def filter_queue(queryset, queue: str, now=None):
    now = now or timezone.now()
    opened = open_filter(now)
    if queue == "waiting":
        return queryset.filter(opened).order_by(Coalesce("sent_at", "created_at"), "created_at")
    if queue == "not_started":
        return queryset.filter(opened, started_at__isnull=True).order_by(
            Coalesce("sent_at", "created_at"), "created_at"
        )
    if queue == "in_progress":
        return queryset.filter(opened, started_at__isnull=False).order_by(
            Coalesce("sent_at", "created_at"), "created_at"
        )
    if queue == "review":
        return queryset.filter(
            completed_at__isnull=False, reviewed_at__isnull=True, revoked_at__isnull=True
        ).order_by("completed_at")
    if queue == "attention":
        return queryset.filter(emails__status__in=["failed", "sending"]).distinct().order_by(
            "created_at"
        )
    if queue == "booked":
        return queryset.filter(booking__isnull=False).order_by("-created_at")
    if queue == "finished":
        return queryset.filter(completed_at__isnull=False).order_by("-completed_at")
    return queryset.order_by("-created_at")


def assessment_summary(now=None) -> dict:
    now = now or timezone.now()
    base = visible_invitations()
    opened = open_filter(now)
    counts = base.aggregate(
        assessments_open=Count("pk", filter=opened),
        not_started=Count("pk", filter=opened & Q(started_at__isnull=True)),
        in_progress=Count("pk", filter=opened & Q(started_at__isnull=False)),
        needs_review=Count(
            "pk",
            filter=Q(
                completed_at__isnull=False,
                reviewed_at__isnull=True,
                revoked_at__isnull=True,
            ),
        ),
        finished=Count("pk", filter=Q(completed_at__isnull=False)),
        booked=Count("pk", filter=Q(booking__isnull=False)),
        total=Count("pk"),
    )
    attention = (
        base.filter(emails__status__in=["failed", "sending"]).values("pk").distinct().count()
    )
    oldest = base.filter(opened).aggregate(
        oldest=Min(Coalesce("sent_at", "created_at"))
    )["oldest"]
    tab_counts = {
        "waiting": counts["assessments_open"],
        "not_started": counts["not_started"],
        "in_progress": counts["in_progress"],
        "review": counts["needs_review"],
        "attention": attention,
        "booked": counts["booked"],
        "finished": counts["finished"],
        "all": counts["total"],
    }
    return {
        "people_waiting": pending_completion_people_count(now),
        "assessments_open": counts["assessments_open"],
        "needs_review": counts["needs_review"],
        "email_attention": attention,
        "oldest_waiting": waiting_label(oldest, now) if oldest else "",
        "tabs": [
            {"key": key, "label": label, "count": tab_counts[key]}
            for key, label in QUEUE_CHOICES
        ],
    }


def family_open_counts(parent_ids, now=None) -> dict[int, int]:
    if not parent_ids:
        return {}
    rows = (
        visible_invitations()
        .filter(open_filter(now), child__parent_id__in=parent_ids)
        .values("child__parent_id")
        .annotate(total=Count("pk"))
    )
    return {row["child__parent_id"]: row["total"] for row in rows}


def review_preview(limit=5, now=None) -> list[dict]:
    """Oldest finished inventories that still need a person to read them."""
    now = now or timezone.now()
    invitations = (
        visible_invitations()
        .filter(completed_at__isnull=False, reviewed_at__isnull=True, revoked_at__isnull=True)
        .select_related("child__parent")
        .order_by("completed_at")[:limit]
    )
    rows = []
    for invitation in invitations:
        result = invitation.result if isinstance(invitation.result, dict) else {}
        rows.append(
            {
                "lead": invitation.child.parent,
                "invitation": invitation,
                "child_name": invitation.child.name,
                "outcome_label": OUTCOME_LABELS.get(result.get("outcome"), ""),
                "waiting_label": waiting_label(invitation.completed_at, now),
            }
        )
    return rows


def waiting_preview(limit=5, now=None) -> list[dict]:
    """Oldest families who still need to finish, for the CRM overview."""
    now = now or timezone.now()
    people = []
    seen = {}
    invitations = (
        visible_invitations()
        .filter(open_filter(now))
        .select_related("child__parent")
        .order_by(Coalesce("sent_at", "created_at"), "created_at")
    )
    for invitation in invitations:
        parent = invitation.child.parent
        person = seen.get(parent.pk)
        if person is None:
            if len(people) >= limit:
                continue
            person = {
                "lead": parent,
                "invitation": invitation,
                "waiting_since": invitation.sent_at or invitation.created_at,
                "children": [],
                "open_count": 0,
            }
            seen[parent.pk] = person
            people.append(person)
        person["children"].append(invitation.child.name)
        person["open_count"] += 1
    for person in people:
        person["waiting_label"] = waiting_label(person["waiting_since"], now)
        person["child_label"] = ", ".join(person["children"])
    return people


def _owner_label(user) -> str:
    if not user:
        return "Unassigned"
    return user.get_full_name() or user.email or "Unassigned"


def _section_progress(invitation) -> str:
    result = invitation.result if isinstance(invitation.result, dict) else {}
    if invitation.completed_at:
        answered = result.get("answered")
        total = result.get("total")
        yes_count = result.get("yes_count")
        if answered is not None and total is not None:
            return f"{yes_count} Yes · {answered} of {total} questions answered"
        return "Finished"
    if invitation.revoked_at:
        return "The link was withdrawn before the parent finished."
    if not invitation.started_at:
        if invitation.sent_at:
            return "Sent. The parent has not opened it."
        return "Created. Email delivery is not confirmed yet."
    try:
        groups = definition(invitation.child.grade)["groups"]
    except InventoryError:
        return "Started"
    total = len(groups)
    saved = min(invitation.current_group, total)
    if saved <= 0:
        return f"Opened · 0 of {total} sections saved"
    if saved >= total:
        return f"All {total} sections saved · not submitted"
    title = groups[saved - 1].get("title", "")
    detail = f" · last saved: {title}" if title else ""
    return f"{saved} of {total} sections saved{detail}"


def _email_needs_attention(emails) -> bool:
    return any(email.status in {"failed", "sending"} for email in emails)


def _flow_steps(invitation, now) -> list[dict]:
    expired = bool(
        invitation.expires_at and invitation.expires_at <= now and not invitation.completed_at
    )
    if invitation.sent_at:
        send = {"label": "Send", "detail": "Delivered", "state": "done"}
    else:
        send = {"label": "Send", "detail": "Waiting on email", "state": "current"}

    if invitation.revoked_at and not invitation.completed_at:
        parent = {"label": "Parent finishes", "detail": "Link withdrawn", "state": "stopped"}
    elif expired:
        parent = {"label": "Parent finishes", "detail": "Link expired", "state": "stopped"}
    elif invitation.completed_at:
        parent = {"label": "Parent finishes", "detail": "Submitted", "state": "done"}
    elif invitation.started_at:
        parent = {"label": "Parent finishes", "detail": "In progress", "state": "current"}
    elif invitation.sent_at:
        parent = {"label": "Parent finishes", "detail": "Not started", "state": "current"}
    else:
        parent = {"label": "Parent finishes", "detail": "After the email sends", "state": "upcoming"}

    if invitation.reviewed_at:
        review = {"label": "Review", "detail": "Reviewed", "state": "done"}
    elif invitation.completed_at:
        review = {"label": "Review", "detail": "Needs a person", "state": "current"}
    else:
        review = {"label": "Review", "detail": "After submit", "state": "upcoming"}

    result = invitation.result if isinstance(invitation.result, dict) else {}
    outcome = result.get("outcome")
    booked = _has_booking(invitation)
    if booked:
        nxt = {"label": "Next step", "detail": "Consultation booked", "state": "done"}
    elif invitation.reviewed_at and outcome == "resources":
        nxt = {"label": "Next step", "detail": "Resources", "state": "done"}
    elif invitation.reviewed_at and outcome == "support":
        nxt = {"label": "Next step", "detail": "Book a consultation", "state": "current"}
    elif invitation.reviewed_at and outcome == "review":
        nxt = {"label": "Next step", "detail": "Choose the recommendation", "state": "current"}
    elif invitation.reviewed_at:
        nxt = {"label": "Next step", "detail": "Follow up", "state": "current"}
    else:
        nxt = {"label": "Next step", "detail": "After review", "state": "upcoming"}
    return [send, parent, review, nxt]


def _has_booking(invitation) -> bool:
    try:
        return invitation.booking is not None
    except ObjectDoesNotExist:
        return False


def _queue_for(key: str) -> str:
    return {
        "review": "review",
        "reviewed": "finished",
        "in_progress": "in_progress",
        "not_started": "not_started",
        "expired": "all",
        "revoked": "all",
    }.get(key, "waiting")


def assessment_snapshot(invitation, now=None, emails=None, family_open_count=1) -> dict:
    now = now or timezone.now()
    if emails is None:
        emails = list(invitation.emails.all())
    result = invitation.result if isinstance(invitation.result, dict) else {}
    expired = bool(
        invitation.expires_at and invitation.expires_at <= now and not invitation.completed_at
    )
    if invitation.revoked_at and not invitation.completed_at:
        key, label, badge, action = "revoked", "Withdrawn", "badge-neutral", "View assessment"
    elif invitation.completed_at and not invitation.reviewed_at:
        key, label, badge, action = "review", "Needs review", "badge-warn", "Review results"
    elif invitation.completed_at:
        key, label, badge, action = "reviewed", "Reviewed", "badge", "View results"
    elif expired:
        key, label, badge, action = "expired", "Link expired", "badge-neutral", "View assessment"
    elif invitation.started_at:
        key, label, badge, action = "in_progress", "In progress", "badge-warn", "View progress"
    elif invitation.sent_at:
        key, label, badge, action = "not_started", "Not started", "badge-neutral", "View assessment"
    else:
        key, label, badge, action = (
            "awaiting",
            "Awaiting delivery",
            "badge-neutral",
            "View assessment",
        )

    if key in {"review", "in_progress", "not_started", "awaiting"}:
        action_class = "btn btn-primary btn-small"
    else:
        action_class = "btn btn-small"

    since = invitation.sent_at or invitation.created_at
    if invitation.completed_at:
        when_kind, when_at = "finished", invitation.completed_at
    elif invitation.revoked_at:
        when_kind, when_at = "withdrawn", invitation.revoked_at
    elif expired:
        when_kind, when_at = "expired", invitation.expires_at
    else:
        when_kind, when_at = "waiting", since

    return {
        "invitation": invitation,
        "key": key,
        "label": label,
        "badge": badge,
        "action": action,
        "action_class": action_class,
        "progress": _section_progress(invitation),
        "queue": _queue_for(key),
        "owner": _owner_label(invitation.child.parent.assigned_to),
        "email_attention": _email_needs_attention(emails),
        "outcome_label": OUTCOME_LABELS.get(result.get("outcome"), ""),
        "family_open_count": family_open_count,
        "when_kind": when_kind,
        "when_at": when_at,
        "waiting_label": waiting_label(since, now) if when_kind == "waiting" else "",
        "steps": _flow_steps(invitation, now),
        "booked": _has_booking(invitation),
    }
