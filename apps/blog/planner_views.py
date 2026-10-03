from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.blog.models import BlogContentPlan as ContentPlan
from apps.blog.models import BlogContentWeek as ContentWeek
from apps.blog.models import BlogPost
from apps.blog.planner import (
    get_plan,
    request_preview,
    retry_week,
    save_plan,
    schedule_week,
    skip_week,
)
from apps.blog.promotion import feature_article
from apps.social.access import SocialRequest, social_editor_required
from apps.social.ai import ai_configured
from apps.social.editorial import PILLARS, SOURCES
from apps.social.exceptions import SocialError
from apps.social.models import SocialPost
from apps.social.services import EASTERN

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
            "promote_facebook",
            "promotion_delay_hours",
            "priorities",
        )
        labels: ClassVar = {
            "mode": "Publishing mode",
            "weekday": "Day of the week",
            "posting_hour": "Article publication time",
            "promote_facebook": "Feature each article on Facebook",
            "promotion_delay_hours": "Hours after the article goes live (1–72)",
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
            return redirect("blog_manage:planner")
    weeks = list(
        ContentWeek.objects.filter(planned_at__gte=timezone.now() - timedelta(days=7))
        .select_related("post", "post__facebook_promotion")
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
            "promotion": getattr(week.post, "facebook_promotion", None)
            if week.post_id
            else None,
            "pillar_label": dict(PILLARS).get(week.pillar, week.pillar),
        }
        for week in weeks
    ]
    return render(
        request,
        "blog/manage/planner.html",
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
                "Automation paused. Future automatic articles and Facebook features are held for review. Published articles stay live; a feature already sending cannot be recalled.",
            )
        elif action in {"skip", "retry", "schedule"}:
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
            elif action == "schedule":
                schedule_week(week.pk, actor=request.user)
                messages.success(
                    request,
                    "Article scheduled. Its Facebook feature is in the marketing schedule.",
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
    return redirect("blog_manage:planner")


class FeatureForm(forms.Form):
    caption = forms.CharField(
        max_length=1800,
        min_length=8,
        label="Facebook teaser",
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    scheduled_at = forms.DateTimeField(
        label="Feature on Facebook (Eastern time)",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "focus-ring w-full rounded-xl border border-gray-300 px-3 py-2"
            )


@social_editor_required
@require_http_methods(["GET", "POST"])
def facebook_feature(request: SocialRequest, pk: int) -> HttpResponse:
    article = get_object_or_404(BlogPost, pk=pk)
    promotion = SocialPost.objects.filter(blog_post=article).first()
    when = (
        promotion.scheduled_at
        if promotion and promotion.scheduled_at
        else max(article.published_at or timezone.now(), timezone.now())
        + timedelta(hours=1)
    )
    with timezone.override(EASTERN):
        form = FeatureForm(
            request.POST if request.method == "POST" else None,
            initial={
                "caption": promotion.facebook_caption if promotion else article.excerpt,
                "scheduled_at": when.astimezone(EASTERN).strftime("%Y-%m-%dT%H:%M"),
            },
        )
        if request.method == "POST" and form.is_valid():
            try:
                feature_article(
                    article,
                    caption=form.cleaned_data["caption"],
                    when=form.cleaned_data["scheduled_at"],
                    actor=request.user,
                )
            except SocialError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(
                    request, "Facebook feature scheduled with the article link."
                )
                return redirect("social:queue")
    return render(
        request,
        "blog/manage/facebook_feature.html",
        {"article": article, "promotion": promotion, "form": form},
    )
