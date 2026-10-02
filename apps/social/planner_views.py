from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.social.access import SocialRequest, social_editor_required
from apps.social.ai import ai_configured
from apps.social.editorial import PILLARS, SOURCES
from apps.social.exceptions import SocialError
from apps.social.models import ContentPlan, ContentWeek
from apps.social.planner import (
    get_plan,
    request_preview,
    retry_week,
    save_plan,
    skip_week,
)

if TYPE_CHECKING:
    PlanFormBase = forms.ModelForm[ContentPlan]
else:
    PlanFormBase = forms.ModelForm


class PlanForm(PlanFormBase):
    weekday = forms.TypedChoiceField(
        choices=list(
            enumerate(
                (
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                )
            )
        ),
        coerce=int,
    )
    posting_hour = forms.TypedChoiceField(
        choices=[
            (hour, f"{hour % 12 or 12}:00 {'AM' if hour < 12 else 'PM'} Eastern")
            for hour in range(8, 21)
        ],
        coerce=int,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "focus-ring rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm max-w-full"
            )

    class Meta:
        model = ContentPlan
        fields = (
            "mode",
            "weekday",
            "posting_hour",
            "audience",
            "post_to_facebook",
            "post_to_instagram",
            "priorities",
        )
        labels: ClassVar = {
            "mode": "Posting mode",
            "weekday": "Day of the week",
            "posting_hour": "Posting time",
            "post_to_facebook": "Post to Facebook",
            "post_to_instagram": "Post to Instagram",
            "priorities": "Topics to emphasize or avoid",
        }
        widgets: ClassVar = {
            "priorities": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Optional: topics to emphasize or avoid. Add public information only.",
                }
            )
        }


@social_editor_required
@require_http_methods(["GET", "POST"])
def planner(request: SocialRequest) -> HttpResponse:
    plan = get_plan()
    form = PlanForm(request.POST if request.method == "POST" else None, instance=plan)
    if request.method == "POST" and form.is_valid():
        try:
            save_plan(values=form.cleaned_data, actor=request.user)
        except SocialError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(
                request,
                "Content plan saved. Prepared posts keep their current content and date.",
            )
            return redirect("social:planner")
    weeks = list(
        ContentWeek.objects.filter(planned_at__gte=timezone.now() - timedelta(days=7))
        .select_related("post")
        .order_by("planned_at")[:8]
    )
    cards = [
        {
            "week": week,
            "sources": [
                SOURCES[key]
                for key in week.content.get("source_ids", [])
                if key in SOURCES
            ],
            "pillar_label": dict(PILLARS).get(week.pillar, week.pillar),
        }
        for week in weeks
    ]
    return render(
        request,
        "social/planner.html",
        {
            "plan": plan,
            "form": form,
            "cards": cards,
            "sources": SOURCES.values(),
            "ai_ready": ai_configured(),
            "worker_stale": plan.mode != ContentPlan.Mode.PAUSED
            and (
                plan.last_worker_at is None
                or plan.last_worker_at < timezone.now() - timedelta(minutes=20)
            ),
        },
    )


@social_editor_required
@require_POST
def plan_action(request: SocialRequest) -> HttpResponse:
    try:
        action = request.POST.get("action")
        if action == "preview":
            request_preview(actor=request.user)
            messages.success(
                request,
                "Four upcoming weeks are queued. The background worker prepares one at a time; previews appear here as they finish.",
            )
        elif action == "pause":
            plan = get_plan()
            values = {field: getattr(plan, field) for field in PlanForm.Meta.fields}
            values["mode"] = ContentPlan.Mode.PAUSED
            save_plan(values=values, actor=request.user)
            ContentPlan.objects.filter(pk=1).update(preview_requested=False)
            messages.success(
                request,
                "Automation paused. Its queued posts are now drafts; a post already sending cannot be recalled.",
            )
        elif action in {"skip", "retry"}:
            week_id = request.POST.get("week", "")
            if not week_id.isdecimal():
                raise SocialError("Choose a valid week.")
            week = get_object_or_404(ContentWeek, pk=int(week_id))
            if action == "skip":
                skip_week(week.pk, actor=request.user)
                messages.success(
                    request,
                    "This week is skipped. It will not be automatically replaced.",
                )
            else:
                retry_week(week.pk, actor=request.user)
                messages.success(
                    request, "The failed week is queued for another attempt."
                )
        else:
            raise SocialError("Choose a supported action.")
    except SocialError as exc:
        messages.error(request, str(exc))
    return redirect("social:planner")
