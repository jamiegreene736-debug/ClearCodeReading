"""Preview approved first-stage copy and run internal tests without customer delivery."""

import uuid
from typing import TYPE_CHECKING, Any, ClassVar

from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.crm.models import Lead, Opportunity
from apps.crm_email.models import Mailbox, Message, StageEmailDelivery, StageEmailPilot
from apps.crm_email.security import EmailError, EmailRequest, require_configured
from apps.crm_email.services import active_mailbox
from apps.crm_email.stage_emails import TEST_RECIPIENT, render_copy, sending_mailbox
from apps.crm_email.views import crm_view, hub_context
from apps.users.models import AuditLog

if TYPE_CHECKING:
    PilotFormBase = forms.ModelForm[StageEmailPilot]
else:
    PilotFormBase = forms.ModelForm


class PilotForm(PilotFormBase):
    class Meta:
        model = StageEmailPilot
        fields: ClassVar = [
            "mailbox",
            "equity_mailbox",
            "equity_signature",
            "enabled",
            "scheduling_link",
            "bethany_signature",
            "foundation_name",
            "sample_company",
            "sample_investment_category",
        ]
        labels: ClassVar = {
            "mailbox": "Bethany’s connected sending mailbox",
            "equity_mailbox": "Equity sending mailbox",
            "equity_signature": "Equity sender signature",
            "enabled": "Enable first-stage tests to info@clearcodereading.com",
            "scheduling_link": "Bethany’s scheduling link (blank = this site’s /book/ page)",
            "bethany_signature": "Bethany’s signature",
            "foundation_name": "Foundation sender name",
            "sample_company": "Company name for test examples",
            "sample_investment_category": "Investment category for test examples",
        }
        widgets: ClassVar = {
            "equity_signature": forms.Textarea(attrs={"rows": 4}),
            "bethany_signature": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_scheduling_link(self) -> str:
        value: str = self.cleaned_data["scheduling_link"]
        if value and not value.startswith("https://"):
            raise forms.ValidationError("Use Bethany’s full HTTPS scheduling link.")
        return value


def sample_deal(pipeline: str, pilot: StageEmailPilot) -> Opportunity:
    return Opportunity(
        pipeline=pipeline,
        stage=Opportunity.initial_stage_for_pipeline(pipeline),
        lead=Lead(
            contact_name="Test Contact",
            contact_email=TEST_RECIPIENT,
            school_name=pilot.sample_company,
            organization_name=pilot.sample_company,
        ),
        investment_category=pilot.sample_investment_category,
    )


def create_test(pilot: StageEmailPilot, pipeline: str, token: str) -> Opportunity:
    require_configured()
    active_mailbox(sending_mailbox(pipeline, pilot).user)
    try:
        payload = signing.loads(token, salt="first-stage-test", max_age=3600)
        if payload["pilot"] != pilot.pk or payload["pipeline"] != pipeline:
            raise signing.BadSignature
    except (signing.BadSignature, KeyError, TypeError) as exc:
        raise EmailError(
            "This test request expired. Refresh the page and try again."
        ) from exc
    with transaction.atomic():
        pilot = (
            StageEmailPilot.objects.select_for_update(of=("self",))
            .select_related("mailbox__user")
            .get(pk=pilot.pk)
        )
        if not pilot.enabled:
            raise EmailError("Enable first-stage testing before sending an example.")
        previous = Opportunity.objects.filter(metadata__stage_email_test=token).first()
        if previous:
            return previous
        deal = sample_deal(pipeline, pilot)
        copy = render_copy(deal, pilot)
        if copy.missing:
            raise EmailError("Complete before sending: " + ", ".join(copy.missing))
        assert deal.lead is not None
        deal.lead.save()
        deal.owner = sending_mailbox(pipeline, pilot).user
        deal.name = "EMAIL TEST — " + str(Opportunity.Pipeline(pipeline).label)
        deal.metadata = {"stage_email_test": token, "needs_naming_review": True}
        deal.save()
        # The same entry signal handles ordinary UI/API deal creation and this example.
        return deal


@crm_view
@require_http_methods(["GET", "POST"])
def first_stage(request: EmailRequest) -> HttpResponse:
    if not request.user.can_manage_crm_users:
        raise PermissionDenied
    mailbox, _ = Mailbox.objects.get_or_create(user=request.user)
    pilot, _ = StageEmailPilot.objects.get_or_create(
        pk=1, defaults={"mailbox": mailbox}
    )
    mailbox = pilot.mailbox
    form = PilotForm(
        request.POST if request.POST.get("action") == "save" else None, instance=pilot
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save" and form.is_valid():
            if form.cleaned_data["enabled"]:
                require_configured()
                active_mailbox(form.cleaned_data["mailbox"].user)
                if form.cleaned_data["equity_mailbox"]:
                    active_mailbox(form.cleaned_data["equity_mailbox"].user)
            with transaction.atomic():
                # Serialize activation across administrators; exactly one pilot owns intake.
                list(
                    StageEmailPilot.objects.select_for_update(of=("self",)).order_by(
                        "pk"
                    )
                )
                if form.cleaned_data["enabled"]:
                    StageEmailPilot.objects.exclude(pk=pilot.pk).update(enabled=False)
                form.save()
                if not pilot.enabled:
                    pending = StageEmailDelivery.objects.filter(pilot=pilot).filter(
                        Q(message__isnull=True)
                        | Q(message__status__in=["queued", "draft", "failed"])
                    )
                    Message.objects.filter(
                        stage_delivery__in=pending, status="queued"
                    ).update(
                        status=Message.Status.CANCELLED,
                        last_error="First-stage testing was paused.",
                    )
                    pending.filter(message__isnull=True).update(
                        cancelled=True, error="First-stage testing was paused."
                    )
                AuditLog.objects.create(
                    actor=request.user,
                    action="crm.stage_email.pilot_settings",
                    entity_type="crm_email.StageEmailPilot",
                    entity_id=str(pilot.pk),
                    after={
                        "enabled": pilot.enabled,
                        "mailbox_id": pilot.mailbox_id,
                        "equity_mailbox_id": pilot.equity_mailbox_id,
                        "test_recipient": TEST_RECIPIENT,
                    },
                )
            messages.success(
                request,
                "First-stage test settings saved. Customer delivery remains disabled.",
            )
            return redirect("crm_first_stage_emails")
        if action == "test":
            pipeline = request.POST.get("pipeline", "")
            if pipeline not in Opportunity.Pipeline.values:
                raise EmailError("Choose one of the five deal pipelines.")
            deal = create_test(pilot, pipeline, request.POST.get("token", ""))
            AuditLog.objects.create(
                actor=request.user,
                action="crm.stage_email.test_created",
                entity_type="crm.Opportunity",
                entity_id=str(deal.pk),
                after={"pipeline": pipeline, "test_recipient": TEST_RECIPIENT},
            )
            messages.success(
                request,
                f"Test deal #{deal.pk} created. Check delivery status below; queued does not mean delivered.",
            )
            return redirect("crm_first_stage_emails")
    previews: list[dict[str, Any]] = []
    for pipeline, label in Opportunity.Pipeline.choices:
        copy = render_copy(sample_deal(pipeline, pilot), pilot)
        previews.append(
            {
                "pipeline": pipeline,
                "label": label,
                "stage": Opportunity.initial_stage_for_pipeline(pipeline),
                "copy": copy,
                "token": signing.dumps(
                    {
                        "pilot": pilot.pk,
                        "pipeline": pipeline,
                        "nonce": uuid.uuid4().hex,
                    },
                    salt="first-stage-test",
                ),
            }
        )
    response = render(
        request,
        "crm/first_stage_emails.html",
        {
            "active_tab": "tests",
            **hub_context(request),
            "form": form,
            "pilot": pilot,
            "mailbox": mailbox,
            "previews": previews,
            "recipient": TEST_RECIPIENT,
            "deliveries": StageEmailDelivery.objects.filter(pilot=pilot)
            .select_related("deal", "lead", "message")
            .order_by("-pk")[:30],
        },
    )
    response["Cache-Control"] = "private, no-store"
    return response
