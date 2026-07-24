from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import F, Q
from django.db.models.functions import TruncDate
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import IsEvaluator, user_can_log_session
from apps.schools.models import SchoolMembership
from apps.sessions.models import Session, SessionTemplate, SkillObservation
from apps.sessions.rapid_logging import build_rapid_defaults
from apps.sessions.serializers import (
    RapidSessionLogSerializer,
    SessionSerializer,
    SessionTemplateSerializer,
    SkillObservationSerializer,
)
from apps.users.models import ChildProfile, CustomUser


def _accessible_center_filter(user):
    if not getattr(user, "is_authenticated", False):
        return Q(pk__in=[])
    if getattr(user, "is_superuser", False) or getattr(user, "role", None) == CustomUser.Role.SUPER_ADMIN:
        return Q()
    return Q(center__memberships__user=user, center__memberships__is_deleted=False)


class IsSessionTemplateManager(IsEvaluator):
    """Evaluator role plus the view's center-scoped object lookup."""

    def has_object_permission(self, request, view, obj):
        return True


class SessionViewSet(viewsets.ModelViewSet):
    serializer_class = SessionSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        queryset = (
            Session.objects.filter(is_deleted=False)
            .filter(_accessible_center_filter(self.request.user))
            .select_related(
                "center",
                "child",
                "specialist",
                "curriculum_position__curriculum",
                "session_template",
            )
            .prefetch_related("targeted_positions", "revision_history")
            .distinct()
        )
        child_id = self.request.query_params.get("child")
        if child_id:
            queryset = queryset.filter(child_id=child_id)
        return queryset

    @action(detail=False, methods=["get"], url_path="defaults")
    def defaults(self, request):
        child_id = request.query_params.get("child")
        if not child_id:
            return Response({"child": "This query parameter is required."}, status=status.HTTP_400_BAD_REQUEST)
        child = ChildProfile.objects.select_related("school").filter(pk=child_id, is_deleted=False).first()
        if child is None:
            return Response({"detail": "Child not found."}, status=status.HTTP_404_NOT_FOUND)
        if not user_can_log_session(request.user, child):
            return Response({"detail": "You are not authorized to log sessions for this reader."}, status=403)
        try:
            return Response(build_rapid_defaults(child, request.user))
        except ValidationError as error:
            return Response({"detail": "; ".join(error.messages)}, status=409)

    @action(detail=False, methods=["post"], url_path="rapid-log")
    def rapid_log(self, request):
        client_request_id = request.data.get("client_request_id")
        if client_request_id:
            duplicate = self.get_queryset().filter(client_request_id=client_request_id).first()
            if duplicate is not None:
                return Response(
                    SessionSerializer(duplicate, context={"request": request}).data,
                    status=status.HTTP_200_OK,
                )
        existing = (
            Session.objects.filter(pk=request.data.get("session_id"), is_deleted=False)
            .select_related("child__school", "specialist")
            .first()
            if request.data.get("session_id")
            else None
        )
        child_id = request.data.get("child") or request.data.get("child_id")
        child = existing.child if existing else ChildProfile.objects.filter(
            pk=child_id, is_deleted=False
        ).select_related("school").first()
        if child and not user_can_log_session(request.user, child, existing):
            return Response({"detail": "You are not authorized to log sessions for this reader."}, status=403)
        serializer = RapidSessionLogSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            saved = serializer.save()
        except IntegrityError:
            if not client_request_id:
                raise
            saved = self.get_queryset().get(client_request_id=client_request_id)
        response_status = status.HTTP_200_OK if existing else status.HTTP_201_CREATED
        return Response(SessionSerializer(saved, context={"request": request}).data, status=response_status)

    @action(detail=False, methods=["get"], url_path="today")
    def today(self, request):
        queryset = self.get_queryset().filter(scheduled_start__date=timezone.localdate())
        if not _has_session_leadership(request.user):
            queryset = queryset.filter(specialist=request.user)
        if request.query_params.get("low_accuracy") in {"1", "true", "yes"}:
            queryset = queryset.filter(accuracy_rate__lt=80)
        return Response(SessionSerializer(queryset.order_by("scheduled_start"), many=True, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="logging-metrics")
    def logging_metrics(self, request):
        queryset = self.get_queryset().filter(status=Session.Status.COMPLETED)
        total = queryset.count()
        same_day = (
            queryset.exclude(ended_at__isnull=True)
            .annotate(logged_date=TruncDate("created_at"), completed_date=TruncDate("ended_at"))
            .filter(logged_date=F("completed_date"))
            .count()
        )
        return Response(
            {
                "completed_sessions": total,
                "same_day_logged": same_day,
                "same_day_rate": round((same_day / total) * 100, 2) if total else None,
                "target_rate": 95,
            }
        )


class SessionTemplateViewSet(viewsets.ModelViewSet):
    serializer_class = SessionTemplateSerializer
    permission_classes = [IsAuthenticated, IsSessionTemplateManager]

    def get_queryset(self):
        queryset = (
            SessionTemplate.objects.filter(is_deleted=False)
            .filter(_accessible_center_filter(self.request.user))
            .select_related("center", "curriculum", "curriculum_position")
            .distinct()
        )
        filters = {
            "curriculum_id": self.request.query_params.get("curriculum"),
            "curriculum_position_id": self.request.query_params.get("curriculum_position"),
            "session_part": self.request.query_params.get("session_part"),
        }
        for field_name, value in filters.items():
            if value:
                queryset = queryset.filter(**{field_name: value})
        if self.request.query_params.get("active") == "true":
            queryset = queryset.filter(is_active=True)
        return queryset

    def perform_destroy(self, instance):
        instance.updated_by = self.request.user
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.save(update_fields=["updated_by", "is_deleted", "deleted_at", "updated_at"])


class SkillObservationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SkillObservationSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        queryset = (
            SkillObservation.objects.filter(is_deleted=False)
            .filter(_accessible_center_filter(self.request.user))
            .select_related("center", "session", "child", "curriculum_position")
            .distinct()
        )
        filters = {
            "center_id": self.request.query_params.get("center"),
            "child_id": self.request.query_params.get("child"),
            "curriculum_position_id": self.request.query_params.get("curriculum_position"),
            "session_id": self.request.query_params.get("session"),
        }
        for field_name, value in filters.items():
            if value:
                queryset = queryset.filter(**{field_name: value})
        return queryset


def _has_session_leadership(user):
    if user.is_superuser or user.role in {CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN}:
        return True
    return user.school_memberships.filter(
        role__in=[SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN],
        is_deleted=False,
    ).exists()


class RapidSessionLogView(LoginRequiredMixin, TemplateView):
    template_name = "sessions/rapid_log.html"
    login_url = "/login/"

    def post(self, request, *args, **kwargs):
        data = {
            "mode": "quick_complete",
            "child_id": request.POST.get("child_id"),
            "accuracy_numerator": request.POST.get("accuracy_numerator"),
            "accuracy_denominator": request.POST.get("accuracy_denominator"),
            "duration_minutes": request.POST.get("duration_minutes") or 60,
            "activity_codes": request.POST.getlist("activity_codes"),
            "error_pattern_codes": request.POST.getlist("error_pattern_codes"),
            "behavioral_observation_codes": request.POST.getlist("behavioral_observation_codes"),
            "behavioral_rating": request.POST.get("behavioral_rating") or "consistent",
            "next_session_direction": request.POST.get("next_session_direction", ""),
            "home_practice_suggestion": request.POST.get("home_practice_suggestion", ""),
            "notes": request.POST.get("notes", ""),
        }
        if request.POST.get("session_id"):
            data["session_id"] = request.POST["session_id"]
        session = self._session_from_id(data.get("session_id"))
        child = session.child if session else ChildProfile.objects.filter(
            pk=data.get("child_id"),
            is_deleted=False,
        ).select_related("school").first()
        if child and user_can_log_session(request.user, child, session):
            defaults = build_rapid_defaults(child, request.user, session)
            for field in ("next_session_direction", "home_practice_suggestion"):
                if data[field].strip() == defaults[field].strip():
                    data[field] = ""
        serializer = RapidSessionLogSerializer(data=data, context={"request": request})
        if serializer.is_valid():
            session = serializer.save()
            messages.success(request, f"Session logged for {session.child}.")
            return redirect(f"{reverse('rapid_session_log')}?{urlencode({'success': 1, 'session': session.id})}")
        return self.render_to_response(self._context(request.POST, serializer.errors), status=400)

    def get_context_data(self, **kwargs):
        return self._context()

    def _context(self, form_data=None, errors=None):
        children = self._children()
        session = self._selected_session()
        child = session.child if session else next(
            (item for item in children if str(item.id) == str(self.request.GET.get("child"))), None
        )
        defaults = build_rapid_defaults(child, self.request.user, session) if child else None
        today = (
            Session.objects.filter(scheduled_start__date=timezone.localdate(), is_deleted=False)
            .filter(_accessible_center_filter(self.request.user))
            .select_related("child", "curriculum_position")
            .order_by("scheduled_start")
            .distinct()
        )
        if not _has_session_leadership(self.request.user):
            today = today.filter(specialist=self.request.user)
        saved = None
        if self.request.GET.get("success") and self.request.GET.get("session"):
            saved = Session.objects.filter(pk=self.request.GET["session"], is_deleted=False).select_related(
                "child__school", "curriculum_position"
            ).first()
            if saved and not user_can_log_session(self.request.user, saved.child, saved):
                saved = None
        return {
            "children": children,
            "selected_child": child,
            "selected_session": session,
            "defaults": defaults,
            "today_sessions": today,
            "saved_session": saved,
            "form_errors": errors,
            "selected_activity_codes": set(
                form_data.getlist("activity_codes") if form_data else (defaults or {}).get("suggested_activity_codes", [])
            ),
            "selected_error_codes": set(form_data.getlist("error_pattern_codes") if form_data else []),
            "selected_behavior_codes": set(form_data.getlist("behavioral_observation_codes") if form_data else []),
            "accuracy_numerator": form_data.get("accuracy_numerator", "") if form_data else "",
            "accuracy_denominator": form_data.get("accuracy_denominator", "10") if form_data else "10",
            "duration_minutes": form_data.get("duration_minutes", "60") if form_data else "60",
            "next_session_direction": form_data.get("next_session_direction", "") if form_data else (defaults or {}).get("next_session_direction", ""),
            "home_practice_suggestion": form_data.get("home_practice_suggestion", "") if form_data else (defaults or {}).get("home_practice_suggestion", ""),
            "notes": form_data.get("notes", "") if form_data else "",
        }

    def _children(self):
        queryset = ChildProfile.objects.filter(
            curriculum_placements__is_active=True,
            curriculum_placements__is_deleted=False,
            is_deleted=False,
        )
        if not self.request.user.is_superuser:
            queryset = queryset.filter(
                school__memberships__user=self.request.user,
                school__memberships__is_deleted=False,
            )
        return [
            child for child in queryset.select_related("school").distinct().order_by("last_name", "first_name")
            if user_can_log_session(self.request.user, child)
        ]

    def _selected_session(self):
        if not self.request.GET.get("session") or self.request.GET.get("success"):
            return None
        session = Session.objects.filter(pk=self.request.GET["session"], is_deleted=False).select_related(
            "child__school", "specialist", "curriculum_position__curriculum"
        ).first()
        return session if session and user_can_log_session(self.request.user, session.child, session) else None

    @staticmethod
    def _session_from_id(session_id):
        if not session_id:
            return None
        return Session.objects.filter(pk=session_id, is_deleted=False).select_related(
            "child__school", "specialist", "curriculum_position__curriculum"
        ).first()
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
