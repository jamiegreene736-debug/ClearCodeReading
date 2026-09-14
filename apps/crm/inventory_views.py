import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.core import signing
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from apps.crm.inventory import (
    GRADES,
    InventoryError,
    calendar_text,
    complete_inventory,
    definition,
    deliver_pending,
    evaluate,
    invitation_url,
    log_activity,
    queue_mail,
    resolve_token,
)
from apps.crm.inventory_forms import BookingForm, InvitationForm, SectionForm, SlotForm
from apps.crm.inventory_models import (
    ConsultationSlot,
    InventoryBooking,
    InventoryChild,
    InventoryInvitation,
    InventoryMail,
)
from apps.crm.models import CrmActivity, Lead
from apps.crm.views import CrmAccessMixin
from apps.users.models import CustomUser


class InventoryListView(CrmAccessMixin, View):
    def get(self, request):
        items = (
            InventoryInvitation.objects.filter(child__parent__is_deleted=False)
            .select_related("child__parent", "child__parent__assigned_to")
            .prefetch_related("emails")
        )
        status = request.GET.get("status", "")
        if status == "pending":
            items = items.filter(completed_at__isnull=True, revoked_at__isnull=True)
        elif status == "completed":
            items = items.filter(completed_at__isnull=False)
        elif status == "review":
            items = items.filter(completed_at__isnull=False, reviewed_at__isnull=True)
        elif status == "booked":
            items = items.filter(booking__isnull=False)
        elif status == "failed":
            items = items.filter(emails__status__in=["failed", "sending"]).distinct()
        return render(
            request,
            "crm/inventory_list.html",
            {
                "page": Paginator(items, 30).get_page(request.GET.get("page")),
                "status": status,
            },
        )


class InventorySendView(CrmAccessMixin, View):
    def get(self, request, pk):
        parent = get_object_or_404(Lead, pk=pk, is_deleted=False)
        form = InvitationForm(
            parent=parent,
            initial={
                "recipient": parent.contact_email,
                "message": f"Hi {parent.contact_name},\n\nPlease complete our Parent Reading Inventory to help us understand your child’s reading. You can save your answers and return using the same link.\n\nThank you,\nThe ClearCode Reading team",
            },
        )
        nonce = signing.dumps(
            {"parent": pk, "user": request.user.pk, "nonce": str(uuid.uuid4())},
            salt="inventory-send",
        )
        return render(
            request,
            "crm/inventory_send.html",
            {"lead": parent, "form": form, "nonce": nonce},
        )

    def post(self, request, pk):
        parent = get_object_or_404(Lead, pk=pk, is_deleted=False)
        form = InvitationForm(request.POST, parent=parent)
        nonce = request.POST.get("nonce", "")
        try:
            payload = signing.loads(nonce, salt="inventory-send", max_age=3600)
            if (
                payload.get("parent") != pk
                or payload.get("user") != request.user.pk
                or not payload.get("nonce")
            ):
                raise signing.BadSignature()
        except signing.BadSignature:
            messages.error(request, "This form expired. Please start a new invitation.")
            return redirect("inventory_send", pk=pk)
        if form.is_valid():
            # Persist the signed form identity to make duplicate submissions idempotent.
            import hashlib

            key = "inventory_send_" + hashlib.sha256(nonce.encode()).hexdigest()
            with transaction.atomic():
                parent = Lead.objects.select_for_update().get(pk=pk, is_deleted=False)
                # Signed nonce is persisted with the outbox key for concurrent requests.
                existing = InventoryMail.objects.filter(
                    key=key, invitation__child__parent=parent
                ).first()
                if existing:
                    invitation = existing.invitation
                else:
                    data = form.cleaned_data
                    child = data["child"] or InventoryChild.objects.create(
                        parent=parent,
                        name=data["child_name"],
                        grade=data["grade"],
                        home_zip=data["home_zip"],
                    )
                    invitation = InventoryInvitation.objects.create(
                        child=child,
                        created_by=request.user,
                        recipient=data["recipient"],
                        expires_at=timezone.now() + timedelta(days=30),
                    )
                    queue_mail(
                        invitation,
                        key,
                        data["recipient"],
                        data["subject"],
                        data["message"],
                        invitation_url(invitation),
                        "Complete assessment",
                    )
                    log_activity(invitation, "Invitation created", request.user)
            deliver_pending(invitation)
            if invitation.emails.filter(status="sent").exists():
                InventoryInvitation.objects.filter(pk=invitation.pk).update(
                    sent_at=timezone.now()
                )
            return redirect("inventory_detail", pk=invitation.pk)
        return render(
            request,
            "crm/inventory_send.html",
            {"lead": parent, "form": form, "nonce": nonce},
        )


class InventoryDetailView(CrmAccessMixin, View):
    def get(self, request, pk):
        invitation = get_object_or_404(
            InventoryInvitation.objects.select_related("child__parent", "reviewed_by"),
            pk=pk,
            child__parent__is_deleted=False,
        )
        sections = []
        for group in definition(invitation.child.grade)["groups"]:
            sections.append(
                {
                    "title": group["title"],
                    "answers": [
                        {
                            "prompt": q["prompt"],
                            "answer": ("Yes" if invitation.answers[q["id"]] else "No")
                            if q["id"] in invitation.answers
                            else "Not answered",
                        }
                        for q in group["questions"]
                    ],
                }
            )
        return render(
            request,
            "crm/inventory_detail.html",
            {
                "invitation": invitation,
                "sections": sections,
                "emails": invitation.emails.all(),
            },
        )

    def post(self, request, pk):
        with transaction.atomic():
            invitation = get_object_or_404(
                InventoryInvitation.objects.select_for_update(),
                pk=pk,
                child__parent__is_deleted=False,
            )
            action = request.POST.get("action")
            if (
                action in {"retry", "resend"}
                and invitation.created_by_id != request.user.pk
            ):
                messages.error(
                    request,
                    "Only the original sender may retry or resend messages from their mailbox.",
                )
                return redirect("inventory_detail", pk=pk)
            if action == "review" and invitation.completed_at:
                invitation.reviewed_at, invitation.reviewed_by = (
                    timezone.now(),
                    request.user,
                )
                invitation.save()
                CrmActivity.objects.filter(
                    pk=invitation.result.get("review_task_id"),
                    lead=invitation.child.parent,
                    activity_type="task",
                    completed_at__isnull=True,
                ).update(completed_at=timezone.now())
                log_activity(invitation, "Reviewed", request.user)
            elif action == "revoke":
                invitation.revoked_at = timezone.now()
                invitation.save()
                log_activity(invitation, "Link revoked", request.user)
            elif action == "retry":
                mail = get_object_or_404(
                    InventoryMail.objects.select_for_update(),
                    invitation=invitation,
                    pk=request.POST.get("mail"),
                )
                if mail.status in {"failed", "pending"} or (
                    mail.status == "sending"
                    and mail.attempted_at < timezone.now() - timedelta(minutes=5)
                    and request.POST.get("checked_provider") == "yes"
                ):
                    mail.status, mail.error = "pending", ""
                    mail.save()
                    log_activity(invitation, "Email retry requested", request.user)
                else:
                    messages.error(
                        request,
                        "Check provider delivery records before retrying an uncertain message. Allow five minutes for an active send to finish.",
                    )
            elif (
                action == "resend"
                and not invitation.completed_at
                and not invitation.revoked_at
                and invitation.expires_at > timezone.now()
            ):
                recent = invitation.emails.filter(
                    key__startswith="reminder-",
                    created_at__gt=timezone.now() - timedelta(days=1),
                ).exists()
                if not recent:
                    queue_mail(
                        invitation,
                        "reminder-" + timezone.now().strftime("%Y%m%d"),
                        invitation.recipient,
                        "Reminder: your Parent Reading Inventory",
                        "You can complete or continue your Parent Reading Inventory using the link below.",
                        invitation_url(invitation),
                        "Continue assessment",
                    )
                    log_activity(invitation, "Reminder requested", request.user)
                else:
                    messages.info(
                        request,
                        "A reminder has already been requested in the last 24 hours.",
                    )
        deliver_pending(invitation)
        return redirect("inventory_detail", pk=pk)


@method_decorator(never_cache, name="dispatch")
class InventoryPublicView(View):
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response["Referrer-Policy"] = "no-referrer"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response

    def get_invitation(self, token):
        try:
            return resolve_token(token)
        except InventoryError as exc:
            raise Http404(str(exc)) from exc

    def page(self, request, invitation, form=None, saved=False):
        if invitation.completed_at:
            return render(
                request,
                "crm/inventory_public.html",
                {
                    "invitation": invitation,
                    "completed": True,
                    "result": invitation.result,
                },
            )
        index = invitation.current_group
        group = definition(invitation.child.grade)["groups"][index]
        form = form or SectionForm(
            grade=invitation.child.grade,
            group_index=index,
            answers=invitation.answers,
            revision=invitation.revision,
        )
        return render(
            request,
            "crm/inventory_public.html",
            {
                "invitation": invitation,
                "form": form,
                "group": group,
                "step": index + 1,
                "steps": len(definition(invitation.child.grade)["groups"]),
                "saved": saved,
            },
        )

    def get(self, request, token):
        return self.page(
            request, self.get_invitation(token), saved=request.GET.get("saved") == "1"
        )

    def post(self, request, token):
        original = self.get_invitation(token)
        with transaction.atomic():
            invitation = (
                InventoryInvitation.objects.select_for_update()
                .select_related("child__parent")
                .get(pk=original.pk)
            )
            if invitation.revoked_at or invitation.expires_at <= timezone.now():
                raise Http404("Link unavailable")
            if invitation.completed_at:
                return self.page(request, invitation)
            index = invitation.current_group
            form = SectionForm(
                request.POST,
                grade=invitation.child.grade,
                group_index=index,
                answers=invitation.answers,
                revision=invitation.revision,
            )
            if not form.is_valid():
                return self.page(request, invitation, form)
            if form.cleaned_data["revision"] != invitation.revision:
                form.add_error(
                    None,
                    "This page changed in another tab. Reload to see the latest saved answers.",
                )
                return self.page(request, invitation, form)
            answers = dict(invitation.answers)
            for key, value in form.cleaned_data.items():
                if key == "revision":
                    continue
                if value:
                    answers[key] = value == "yes"
                else:
                    answers.pop(key, None)
            saving = request.POST.get("action") == "save"
            ids = [
                q["id"]
                for q in definition(invitation.child.grade)["groups"][index][
                    "questions"
                ]
            ]
            if not saving and any(key not in answers for key in ids):
                form.add_error(
                    None,
                    "Please answer every question in this section, or save and return later.",
                )
                return self.page(request, invitation, form)
            invitation.answers = answers
            invitation.started_at = invitation.started_at or timezone.now()
            invitation.revision += 1
            state = evaluate(invitation.child.grade, answers)
            if not saving and state["complete"]:
                complete_inventory(invitation, state)
            else:
                if not saving:
                    invitation.current_group = state["next_group"]
                invitation.save()
        deliver_pending(invitation)
        return redirect(
            reverse("inventory_public", args=[token]) + ("?saved=1" if saving else "")
        )


class InventorySlotsView(CrmAccessMixin, View):
    def get(self, request):
        return self.page(request, SlotForm())

    def page(self, request, form):
        slots = (
            ConsultationSlot.objects.filter(starts_at__gt=timezone.now())
            .select_related("host", "booking__invitation__child")
            .order_by("starts_at")[:100]
        )
        return render(
            request, "crm/inventory_slots.html", {"form": form, "slots": slots}
        )

    def post(self, request):
        if request.POST.get("action") == "withdraw":
            with transaction.atomic():
                slot = get_object_or_404(
                    ConsultationSlot.objects.select_for_update(),
                    pk=request.POST.get("slot"),
                )
                if not InventoryBooking.objects.filter(slot=slot).exists():
                    slot.active = False
                    slot.save()
                else:
                    messages.error(
                        request,
                        "Booked appointments cannot be withdrawn here. Contact the family to arrange a change.",
                    )
            return redirect("inventory_slots")
        form = SlotForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            local = datetime.combine(data["date"], data["time"]).replace(
                tzinfo=ZoneInfo(data["timezone"])
            )
            end = local + timedelta(minutes=data["duration"])
            # Reject nonexistent and ambiguous wall-clock times around DST changes.
            if (
                local.fold == 0
                and local.utcoffset() != local.replace(fold=1).utcoffset()
            ):
                form.add_error(
                    "time", "Choose a time outside the daylight-saving clock change."
                )
            elif local <= timezone.now():
                form.add_error("date", "Choose a future appointment.")
            else:
                with transaction.atomic():
                    host = CustomUser.objects.select_for_update().get(
                        pk=data["host"].pk
                    )
                    if ConsultationSlot.objects.filter(
                        host=host, active=True, starts_at__lt=end, ends_at__gt=local
                    ).exists():
                        form.add_error(
                            "time",
                            "This appointment overlaps an existing slot for this host.",
                        )
                    else:
                        ConsultationSlot.objects.create(
                            host=host, starts_at=local, ends_at=end
                        )
                        messages.success(request, "Consultation time added.")
                        return redirect("inventory_slots")
        return self.page(request, form)


class InventoryBookingView(InventoryPublicView):
    def booking_page(self, request, invitation, form=None):
        booking = (
            InventoryBooking.objects.filter(invitation=invitation)
            .select_related("slot__host")
            .first()
        )
        slots = ConsultationSlot.objects.filter(
            active=True,
            starts_at__gt=timezone.now(),
            booking__isnull=True,
            host__is_active=True,
            host__is_deleted=False,
        ).select_related("host")[:100]
        form = form or BookingForm()
        form.fields["slot"].choices = [
            (
                str(slot.pk),
                f"{timezone.localtime(slot.starts_at).strftime('%a, %b %d, %Y · %I:%M %p %Z')} — {slot.host.get_full_name() or slot.host.email}",
            )
            for slot in slots
        ]
        return render(
            request,
            "crm/inventory_booking.html",
            {
                "invitation": invitation,
                "form": form,
                "booking": booking,
                "slots": slots,
            },
        )

    def get(self, request, token):
        invitation = self.get_invitation(token)
        if not invitation.completed_at or invitation.result.get("outcome") != "support":
            raise Http404("Consultation link unavailable")
        return self.booking_page(request, invitation)

    def post(self, request, token):
        original = self.get_invitation(token)
        if not original.completed_at or original.result.get("outcome") != "support":
            raise Http404("Consultation link unavailable")
        form = BookingForm(request.POST)
        form.fields["slot"].choices = [
            (str(pk), str(pk))
            for pk in ConsultationSlot.objects.filter(
                active=True,
                starts_at__gt=timezone.now(),
                host__is_active=True,
                host__is_deleted=False,
            ).values_list("pk", flat=True)
        ]
        if form.is_valid():
            with transaction.atomic():
                invitation = InventoryInvitation.objects.select_for_update().get(
                    pk=original.pk
                )
                if invitation.revoked_at or invitation.expires_at <= timezone.now():
                    raise Http404("Link unavailable")
                if InventoryBooking.objects.filter(invitation=invitation).exists():
                    return redirect("inventory_booking", token=token)
                slot = get_object_or_404(
                    ConsultationSlot.objects.select_for_update().select_related("host"),
                    pk=form.cleaned_data["slot"],
                )
                if (
                    not slot.active
                    or slot.starts_at <= timezone.now()
                    or InventoryBooking.objects.filter(slot=slot).exists()
                ):
                    form.add_error(
                        "slot",
                        "That time is no longer available. Please choose another.",
                    )
                else:
                    booking = InventoryBooking.objects.create(
                        invitation=invitation,
                        slot=slot,
                        phone=form.cleaned_data["phone"],
                        timezone=form.cleaned_data["timezone"],
                    )
                    appointment = slot.starts_at.astimezone(
                        ZoneInfo(booking.timezone)
                    ).strftime("%A, %B %d at %I:%M %p %Z")
                    calendar = calendar_text(booking)
                    queue_mail(
                        invitation,
                        "booking-parent",
                        invitation.recipient,
                        "Your ClearCode consultation is booked",
                        f"Your phone consultation is booked for {appointment}. We will call the phone number you provided. A calendar invitation is attached.",
                        calendar=calendar,
                    )
                    queue_mail(
                        invitation,
                        "booking-host",
                        slot.host.email,
                        "A ClearCode consultation is booked",
                        f"A parent has booked a consultation for {appointment}. Open the assessment for contact details.",
                        request.build_absolute_uri(
                            reverse("inventory_detail", args=[invitation.pk])
                        ),
                        "View booking",
                        calendar,
                    )
                    log_activity(invitation, "Consultation booked")
            if not form.errors:
                deliver_pending(invitation)
                return redirect("inventory_booking", token=token)
        return self.booking_page(request, original, form)


class InventoryPreviewView(CrmAccessMixin, View):
    def get(self, request):
        return render(
            request, "crm/inventory_preview.html", {"grades": GRADES.values()}
        )

    def post(self, request):
        grade = request.POST.get("grade", "")
        if grade not in GRADES:
            return self.get(request)
        spec = definition(grade)
        answers = {}
        for group in spec["groups"]:
            for question in group["questions"]:
                value = request.POST.get(question["id"])
                if value in ("yes", "no"):
                    answers[question["id"]] = value == "yes"
            state = evaluate(grade, answers)
            if state["complete"]:
                break
            if state.get("next_group") == spec["groups"].index(group):
                break
        result = evaluate(grade, answers) if answers else None
        return render(
            request,
            "crm/inventory_preview.html",
            {
                "grades": GRADES.values(),
                "grade": grade,
                "spec": spec,
                "result": result,
                "answers": answers,
            },
        )
