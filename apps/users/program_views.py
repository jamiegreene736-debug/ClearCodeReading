"""Dedicated instructional pages. These records stay in the program workspace, not the CRM."""

from urllib.parse import urlencode

from django.db.models import Q
from django.db.models.functions import Lower
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from apps.assessments.models import Assessment, AssessmentResult
from apps.crm.models import Lead
from apps.api.permissions import user_can_log_session
from apps.curriculum.models import LessonTemplate, PlacementRecommendation, StudentPlacement, TeacherLessonTemplate
from apps.sessions.models import Session
from apps.users.models import CustomUser, GuardianRelationship, UserInvitation
from apps.users.portal_views import PortalAuthMixin
from apps.users.program_access import can_manage_invitations, children_for_portal_user, role_flags


def _query_link(params: dict, **updates) -> str:
    merged = {key: value for key, value in {**params, **updates}.items() if value not in ("", None, "all")}
    encoded = urlencode(merged)
    return f"?{encoded}" if encoded else "?"


def _kpi_rows(breakdown) -> list[dict]:
    rows = []
    if not isinstance(breakdown, dict):
        return rows
    for key, value in breakdown.items():
        if isinstance(value, dict):
            label = value.get("label") or key.replace("_", " ").title()
            score = value.get("score")
        else:
            label = str(key).replace("_", " ").title()
            score = value
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = None
        rows.append({"label": label, "score": score})
    return rows


class ProgramPageMixin(PortalAuthMixin):
    audience = "staff"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if getattr(request.user, "role", "") == CustomUser.Role.CRM_USER:
            return redirect("crm_dashboard")
        flags = role_flags(request.user)
        if self.audience == "staff":
            allowed = flags["is_admin"] or flags["is_teacher"]
        elif self.audience == "admin":
            allowed = flags["is_admin"]
        elif self.audience == "family":
            allowed = any(flags.values())
        elif self.audience == "manager":
            allowed = can_manage_invitations(request.user)
        else:
            allowed = False
        if not allowed:
            from django.contrib import messages

            messages.error(request, "That workspace is not part of your account.")
            return redirect("portal_dashboard")
        return super().dispatch(request, *args, **kwargs)

    def base_context(self) -> dict:
        user = self.request.user
        flags = role_flags(user)
        children = children_for_portal_user(user)
        return {**flags, "children": children, "child_count": len(children)}


class PortalReadersView(ProgramPageMixin, TemplateView):
    template_name = "portal/readers.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.base_context())
        user = self.request.user
        children = context["children"]
        query = self.request.GET.get("q", "").strip()
        assignment = self.request.GET.get("assignment", "all")
        if assignment not in {"all", "assigned", "unassigned"}:
            assignment = "all"
        placements = {
            placement.child_id: placement
            for placement in StudentPlacement.objects.filter(child__in=children, is_active=True, is_deleted=False).select_related(
                "current_position", "curriculum"
            )
        }
        latest_sessions = {}
        for session in (
            Session.objects.filter(child__in=children, status=Session.Status.COMPLETED, is_deleted=False)
            .select_related("child")
            .order_by("child_id", "-scheduled_start")
        ):
            latest_sessions.setdefault(session.child_id, session)
        family_leads = {}
        if user.has_crm_access and children:
            guardians = GuardianRelationship.objects.filter(child__in=children, is_deleted=False).select_related("guardian")
            email_to_child = {}
            for relationship in guardians:
                email_to_child.setdefault(relationship.guardian.email.lower(), relationship.child_id)
            if email_to_child:
                leads = (
                    Lead.objects.filter(is_deleted=False)
                    .annotate(email_lower=Lower("contact_email"))
                    .filter(email_lower__in=list(email_to_child))
                    .order_by("-created_at")
                )
                for lead in leads:
                    child_id = email_to_child.get(lead.email_lower)
                    family_leads.setdefault(child_id, lead)
        rows = []
        for child in children:
            profile = child.learning_profile or {}
            teacher_name = profile.get("assigned_teacher_name") or ""
            if query and query.lower() not in f"{child} {teacher_name}".lower():
                continue
            assigned = bool(profile.get("assigned_teacher_id"))
            if assignment == "assigned" and not assigned:
                continue
            if assignment == "unassigned" and assigned:
                continue
            rows.append(
                {
                    "child": child,
                    "teacher_name": teacher_name,
                    "assigned": assigned,
                    "placement": placements.get(child.id),
                    "latest_session": latest_sessions.get(child.id),
                    "can_log_session": user_can_log_session(user, child),
                    "family_lead": family_leads.get(child.id),
                }
            )
        teachers = CustomUser.objects.none()
        if context["is_admin"]:
            teachers = CustomUser.objects.filter(role=CustomUser.Role.TEACHER, is_active=True, is_deleted=False)
            if not user.is_superuser and user.role != CustomUser.Role.SUPER_ADMIN:
                teachers = teachers.filter(
                    school_memberships__school__memberships__user=user,
                    school_memberships__school__memberships__is_deleted=False,
                ).distinct()
        params = {"q": query, "assignment": assignment}
        context.update(
            {
                "reader_rows": rows,
                "teachers": teachers,
                "query": query,
                "assignment": assignment,
                "unassigned_count": sum(1 for row in rows if not row["assigned"]) if assignment == "all" else None,
                "filter_all": _query_link(params, assignment="all"),
                "filter_assigned": _query_link(params, assignment="assigned"),
                "filter_unassigned": _query_link(params, assignment="unassigned"),
                "page_next": self.request.get_full_path(),
            }
        )
        return context


class PortalSessionsView(ProgramPageMixin, TemplateView):
    template_name = "portal/sessions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.base_context())
        children = context["children"]
        status = self.request.GET.get("status", "upcoming")
        if status not in {"upcoming", "completed", "all"}:
            status = "upcoming"
        child_id = self.request.GET.get("child", "").strip()
        selected_child = next((child for child in children if str(child.id) == child_id), None)
        sessions = Session.objects.filter(child__in=children, is_deleted=False).select_related(
            "child", "specialist", "curriculum_position"
        )
        if selected_child:
            sessions = sessions.filter(child=selected_child)
        now = timezone.now()
        if status == "upcoming":
            sessions = sessions.filter(status__in=[Session.Status.SCHEDULED, Session.Status.IN_PROGRESS]).order_by("scheduled_start")
        elif status == "completed":
            sessions = sessions.filter(status=Session.Status.COMPLETED).order_by("-scheduled_start")
        else:
            sessions = sessions.order_by("-scheduled_start")
        params = {"status": status, "child": str(selected_child.id) if selected_child else ""}
        log_url = reverse("rapid_session_log")
        if selected_child:
            log_url = f"{log_url}?child={selected_child.id}"
        context.update(
            {
                "sessions": sessions[:60],
                "status": status,
                "selected_child": selected_child,
                "query_child": child_id,
                "log_url": log_url,
                "filter_upcoming": _query_link(params, status="upcoming"),
                "filter_completed": _query_link(params, status="completed"),
                "filter_all": _query_link(params, status="all"),
                "now": now,
            }
        )
        return context


class PortalPlacementsView(ProgramPageMixin, TemplateView):
    template_name = "portal/placements.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.base_context())
        status = self.request.GET.get("status", "pending")
        if status not in {"pending", "decided", "all"}:
            status = "pending"
        recommendations = (
            PlacementRecommendation.objects.filter(evidence__child__in=context["children"], is_deleted=False)
            .select_related(
                "evidence__child",
                "recommended_curriculum",
                "recommended_position",
                "final_position",
                "confirmed_by",
            )
            .prefetch_related("recommended_sequence__position")
        )
        if status == "pending":
            recommendations = recommendations.filter(status=PlacementRecommendation.Status.PENDING)
        elif status == "decided":
            recommendations = recommendations.exclude(status=PlacementRecommendation.Status.PENDING)
        params = {"status": status}
        context.update(
            {
                "recommendations": recommendations[:40],
                "status": status,
                "filter_pending": _query_link(params, status="pending"),
                "filter_decided": _query_link(params, status="decided"),
                "filter_all": _query_link(params, status="all"),
                "page_next": self.request.get_full_path(),
            }
        )
        return context


class PortalResultsView(ProgramPageMixin, TemplateView):
    audience = "family"
    template_name = "portal/results.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.base_context())
        children = context["children"]
        child_id = self.request.GET.get("child", "").strip()
        selected_child = next((child for child in children if str(child.id) == child_id), None)
        assessments = Assessment.objects.filter(child__in=children, is_deleted=False)
        if selected_child:
            assessments = assessments.filter(child=selected_child)
        results = (
            AssessmentResult.objects.filter(assessment__in=assessments, is_deleted=False)
            .select_related("assessment", "assessment__child")
            .order_by("-created_at")
        )
        rows = []
        for result in results[:40]:
            rows.append({"result": result, "kpis": _kpi_rows(result.category_breakdown)})
        context.update(
            {
                "result_rows": rows,
                "selected_child": selected_child,
                "show_evaluator_notes": context["is_admin"] or context["is_teacher"],
            }
        )
        return context


class PortalInvitationsView(ProgramPageMixin, TemplateView):
    audience = "manager"
    template_name = "portal/invitations.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(role_flags(self.request.user))
        status = self.request.GET.get("status", "all")
        if status not in {"all", "pending", "sent", "accepted", "needs_attention"}:
            status = "all"
        query = self.request.GET.get("q", "").strip()
        invitations = UserInvitation.objects.select_related("user").filter(user__is_deleted=False)
        if not self.request.user.can_manage_crm_users:
            invitations = invitations.filter(
                created_by=self.request.user,
                user__role__in=[CustomUser.Role.TEACHER, CustomUser.Role.GUARDIAN],
            )
        if query:
            invitations = invitations.filter(
                Q(user__email__icontains=query) | Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query)
            )
        counts = {
            "all": invitations.count(),
            "pending": invitations.filter(accepted_at__isnull=True, status__in=["pending", "sending"]).count(),
            "sent": invitations.filter(accepted_at__isnull=True, status="sent").count(),
            "accepted": invitations.filter(accepted_at__isnull=False).count(),
            "needs_attention": invitations.filter(accepted_at__isnull=True, status__in=["failed", "uncertain"]).count(),
        }
        if status == "accepted":
            invitations = invitations.filter(accepted_at__isnull=False)
        elif status == "sent":
            invitations = invitations.filter(accepted_at__isnull=True, status="sent")
        elif status == "pending":
            invitations = invitations.filter(accepted_at__isnull=True, status__in=["pending", "sending"])
        elif status == "needs_attention":
            invitations = invitations.filter(accepted_at__isnull=True, status__in=["failed", "uncertain"])
        params = {"q": query, "status": status}
        context.update(
            {
                "invitations": invitations.order_by("-created_at")[:100],
                "status": status,
                "query": query,
                "counts": counts,
                "filter_all": _query_link(params, status="all"),
                "filter_pending": _query_link(params, status="pending"),
                "filter_sent": _query_link(params, status="sent"),
                "filter_accepted": _query_link(params, status="accepted"),
                "filter_attention": _query_link(params, status="needs_attention"),
            }
        )
        return context
