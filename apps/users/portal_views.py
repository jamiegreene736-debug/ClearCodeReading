from typing import Any

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.db.models import Avg, Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify
from django.views.generic import TemplateView, View

from apps.assessments.models import Assessment, AssessmentResult
from apps.api.permissions import has_coppa_consent, user_can_evaluate_child, user_can_log_session
from apps.curriculum.models import (
    ChildLessonAssignment,
    CurriculumSequence,
    LessonTemplate,
    PlacementRecommendation,
    Skill,
    TeacherLessonTemplate,
)
from apps.curriculum.placement import confirm_recommendation
from apps.curriculum.models import StudentPlacement
from apps.progress.dashboard import build_parent_dashboard
from apps.scheduling.services import operations_metrics, ranked_group_suggestions
from apps.sessions.models import Session
from apps.users.models import ChildProfile, CustomUser, GuardianRelationship


DEMO_LOGINS = {
    "admin": "admin@clearcodereading.com",
    "parent": "parent@clearcodereading.com",
    "teacher": "teacher@clearcodereading.com",
}

DEMO_INBOX_MESSAGES = [
    {
        "sender": "Demo Teacher",
        "audience": "teacher",
        "body": "Hi! Avery did a great job with beginning sounds. I recommend five minutes of repeated reading practice tonight.",
        "sent_at": "Today, 9:15 AM",
    },
    {
        "sender": "Demo Parent",
        "audience": "guardian",
        "body": "Thank you. Should we focus more on fluency or comprehension this week?",
        "sent_at": "Today, 9:22 AM",
    },
    {
        "sender": "Demo Teacher",
        "audience": "teacher",
        "body": "Fluency first. Short familiar passages will help Avery read smoothly and build confidence.",
        "sent_at": "Today, 9:34 AM",
    },
]


def user_can_manage_instruction(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and (
            user.is_superuser
            or user.role in {CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN}
        )
    )


def program_children(user):
    children = ChildProfile.objects.filter(is_deleted=False)
    if not user.is_superuser and user.role != CustomUser.Role.SUPER_ADMIN:
        children = children.filter(
            school__memberships__user=user,
            school__memberships__is_deleted=False,
        )
    return children.distinct().order_by("last_name", "first_name")


def program_teachers(user):
    teachers = CustomUser.objects.filter(role=CustomUser.Role.TEACHER, is_active=True, is_deleted=False)
    if not user.is_superuser and user.role != CustomUser.Role.SUPER_ADMIN:
        teachers = teachers.filter(
            school_memberships__school__memberships__user=user,
            school_memberships__school__memberships__is_deleted=False,
        ).distinct()
    return teachers.order_by("last_name", "first_name", "email")


def reader_assignment(child, teachers_by_id):
    profile = child.learning_profile or {}
    teacher_id = profile.get("assigned_teacher_id")
    teacher = teachers_by_id.get(str(teacher_id)) if teacher_id else None
    assigned_at = parse_datetime(profile.get("assigned_at") or "")
    teacher_name = ""
    if teacher is not None:
        teacher_name = teacher.get_full_name() or teacher.email
    elif teacher_id:
        teacher_name = profile.get("assigned_teacher_name") or profile.get("assigned_teacher_email") or "Assigned teacher"
    return {
        "child": child,
        "teacher": teacher,
        "teacher_name": teacher_name,
        "assigned": bool(teacher_id),
        "assigned_at": assigned_at,
    }


def instruction_workspace_summary(user):
    teachers_by_id = {str(teacher.id): teacher for teacher in program_teachers(user)}
    roster = [reader_assignment(child, teachers_by_id) for child in program_children(user)]
    assigned_reader_count = sum(1 for row in roster if row["assigned"])
    lesson_count = LessonTemplate.objects.filter(is_deleted=False, is_active=True).count()
    shared_lesson_count = TeacherLessonTemplate.objects.filter(is_deleted=False).count()
    return {
        "reader_count": len(roster),
        "assigned_reader_count": assigned_reader_count,
        "unassigned_reader_count": len(roster) - assigned_reader_count,
        "lesson_count": lesson_count,
        "shared_lesson_count": shared_lesson_count,
    }


def _list_field(raw_value, *, limit=12):
    lines = []
    for line in (raw_value or "").splitlines():
        item = " ".join(line.split())
        if item:
            lines.append(item[:240])
        if len(lines) >= limit:
            break
    return lines


def _unique_lesson_slug(title):
    base = (slugify(title) or "lesson")[:140]
    slug = base
    suffix = 2
    while LessonTemplate.objects.filter(slug=slug).exists():
        slug = f"{base}-{suffix}"[:160]
        suffix += 1
    return slug


class PortalLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True
    next_page = reverse_lazy("portal_dashboard")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["demo_access_enabled"] = settings.ENABLE_DEMO_ACCESS
        return context

    def form_valid(self, form):
        from apps.users.onboarding_views import needs_gmail_welcome

        welcome = needs_gmail_welcome(form.get_user())
        messages.success(self.request, "Welcome back to Clear Code Reading.")
        response = super().form_valid(form)
        if welcome:
            self.request.session["onboarding_next"] = response.url
            return redirect("gmail_welcome")
        return response


class DemoLoginView(View):
    def post(self, request: HttpRequest, role: str) -> HttpResponse:
        if not settings.ENABLE_DEMO_ACCESS:
            raise Http404
        email = DEMO_LOGINS.get(role)
        if email is None:
            messages.error(request, "That demo login is not available.")
            return redirect("login")

        User = get_user_model()
        user = User.objects.filter(email=email, is_active=True, is_deleted=False).first()
        if user is None:
            messages.error(
                request,
                "Demo data has not been seeded yet. Run the seed_demo_login command or redeploy once.",
            )
            return redirect("login")

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, f"You are viewing the Demo {role.title()} workspace.")
        return redirect("portal_dashboard")


class PortalAuthMixin:
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse_lazy('login')}?next={request.path}")
        return super().dispatch(request, *args, **kwargs)


class PortalDashboardView(PortalAuthMixin, TemplateView):
    template_name = "portal/dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role == CustomUser.Role.CRM_USER:
            return redirect("crm_dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["is_admin"] = user.is_superuser or user.role in {
            CustomUser.Role.SUPER_ADMIN,
            CustomUser.Role.SCHOOL_ADMIN,
        }
        context["is_parent"] = user.role == CustomUser.Role.GUARDIAN and not context["is_admin"]
        context["is_teacher"] = user.role == CustomUser.Role.TEACHER
        context["is_child"] = user.role == CustomUser.Role.STUDENT

        if context["is_parent"]:
            relationships = GuardianRelationship.objects.filter(
                guardian=user,
                is_deleted=False,
                child__is_deleted=False,
            ).select_related("child")
            children = [relationship.child for relationship in relationships]
        elif context["is_child"]:
            child_profile = getattr(user, "child_profile", None)
            children = [child_profile] if child_profile and not child_profile.is_deleted else []
        elif context["is_teacher"]:
            children = self._children_for_teacher(user)
        else:
            child_queryset = ChildProfile.objects.filter(is_deleted=False)
            if not user.is_superuser and user.role != CustomUser.Role.SUPER_ADMIN:
                child_queryset = child_queryset.filter(
                    school__memberships__user=user,
                    school__memberships__is_deleted=False,
                )
            children = list(child_queryset.distinct().order_by("last_name", "first_name")[:12])

        assessments = (
            Assessment.objects.filter(child__in=children, is_deleted=False)
            .select_related("child", "result")
            .order_by("-survey_completed_at", "-created_at")
        )
        latest_results = (
            AssessmentResult.objects.filter(assessment__in=assessments, is_deleted=False)
            .select_related("assessment", "assessment__child")
            .order_by("-created_at")
        )
        pending_reviews = assessments.filter(status=Assessment.Status.HUMAN_REVIEW)

        lesson_templates = LessonTemplate.objects.filter(is_active=True, is_deleted=False).select_related("skill")
        teacher_template_assignments = TeacherLessonTemplate.objects.filter(is_deleted=False).select_related(
            "teacher", "template", "assigned_by"
        )
        if context["is_teacher"]:
            teacher_template_assignments = teacher_template_assignments.filter(teacher=user)
            available_lesson_templates = [assignment.template for assignment in teacher_template_assignments]
        elif context["is_admin"]:
            available_lesson_templates = lesson_templates
        else:
            available_lesson_templates = LessonTemplate.objects.none()

        child_lesson_assignments = ChildLessonAssignment.objects.filter(
            child__in=children,
            is_deleted=False,
        ).select_related("child", "template", "assigned_by")
        placement_recommendations = (
            PlacementRecommendation.objects.filter(
                evidence__child__in=children,
                status=PlacementRecommendation.Status.PENDING,
                is_deleted=False,
            )
            .select_related("evidence__child", "recommended_curriculum", "recommended_position")
            .prefetch_related("recommended_sequence__position")
        )
        parent_dashboards = []
        if context["is_parent"]:
            allowed_child_ids = {
                relationship.child_id
                for relationship in relationships
                if relationship.consent_status == GuardianRelationship.ConsentStatus.GRANTED
                and relationship.permissions.get("progress_dashboard") is not False
                and has_coppa_consent(relationship.child)
            }
            parent_dashboards = [build_parent_dashboard(child) for child in children if child.id in allowed_child_ids]

        upcoming_sessions = Session.objects.none()
        student_snapshots = []
        if context["is_teacher"] or context["is_admin"]:
            upcoming_sessions = (
                Session.objects.filter(
                    child__in=children,
                    status=Session.Status.SCHEDULED,
                    scheduled_start__gte=timezone.now(),
                    is_deleted=False,
                )
                .select_related("child", "specialist", "curriculum_position")
                .order_by("scheduled_start")[:12]
            )
            placements = {
                placement.child_id: placement
                for placement in StudentPlacement.objects.filter(child__in=children, is_active=True, is_deleted=False)
                .select_related("current_position", "curriculum")
            }
            latest_sessions = {
                session.child_id: session
                for session in Session.objects.filter(child__in=children, status=Session.Status.COMPLETED, is_deleted=False)
                .select_related("child")
                .order_by("child_id", "scheduled_start")
            }
            next_session_by_child = {}
            for session in upcoming_sessions:
                next_session_by_child.setdefault(session.child_id, session)
            student_snapshots = [
                {
                    "child": child,
                    "placement": placements.get(child.id),
                    "latest_session": latest_sessions.get(child.id),
                    "next_session": next_session_by_child.get(child.id),
                    "teacher_name": (child.learning_profile or {}).get("assigned_teacher_name") or "",
                    "unassigned": not (child.learning_profile or {}).get("assigned_teacher_id"),
                    "can_log_session": user_can_log_session(user, child),
                }
                for child in children
            ]
            student_snapshots.sort(
                key=lambda snapshot: (
                    not snapshot["unassigned"],
                    snapshot["child"].last_name,
                    snapshot["child"].first_name,
                )
            )

        operations = None
        grouping_suggestions = []
        if context["is_admin"]:
            center = next((child.school for child in children if child.school_id), None)
            if center is None and not user.is_superuser:
                membership = user.school_memberships.filter(is_deleted=False).select_related("school").first()
                center = membership.school if membership else None
            if center:
                operations = operations_metrics(center)
                grouping_suggestions = ranked_group_suggestions(center)[:8]

        unassigned_readers = [
            child for child in children if not (child.learning_profile or {}).get("assigned_teacher_id")
        ]
        placement_pending_count = placement_recommendations.count()
        teachers = CustomUser.objects.filter(role=CustomUser.Role.TEACHER, is_active=True, is_deleted=False)
        if context["is_admin"] and not user.is_superuser and user.role != CustomUser.Role.SUPER_ADMIN:
            teachers = teachers.filter(
                school_memberships__school__memberships__user=user,
                school_memberships__school__memberships__is_deleted=False,
            ).distinct()

        context.update(
            {
                "children": children,
                "assessments": assessments[:8],
                "latest_results": latest_results[:6],
                "pending_reviews": pending_reviews[:8],
                "assessment_count": assessments.count(),
                "pending_review_count": pending_reviews.count(),
                "average_reading_age": latest_results.aggregate(value=Avg("reading_age"))["value"],
                "child_count": len(children),
                "unassigned_count": len(unassigned_readers),
                "placement_pending_count": placement_pending_count,
                "attention_count": len(unassigned_readers) + placement_pending_count + pending_reviews.count(),
                "kpi_count": self._kpi_count(latest_results.first()),
                "teachers": teachers,
                "inbox_messages": self._inbox_messages()[-2:],
                "lesson_templates": lesson_templates,
                "available_lesson_templates": available_lesson_templates,
                "teacher_template_assignments": teacher_template_assignments[:12],
                "child_lesson_assignments": child_lesson_assignments[:12],
                "placement_recommendations": placement_recommendations[:12],
                "parent_dashboards": parent_dashboards,
                "upcoming_sessions": upcoming_sessions,
                "student_snapshots": student_snapshots,
                "operations_metrics": operations,
                "grouping_suggestions": grouping_suggestions,
                "lesson_count": lesson_templates.count() if context["is_admin"] else 0,
                "shared_lesson_count": teacher_template_assignments.count() if context["is_admin"] else 0,
            }
        )
        return context

    @staticmethod
    def _children_for_teacher(teacher):
        assigned_children = []
        for child in ChildProfile.objects.filter(is_deleted=False).order_by("last_name", "first_name"):
            if str((child.learning_profile or {}).get("assigned_teacher_id")) == str(teacher.id):
                assigned_children.append(child)
        return assigned_children

    @staticmethod
    def _kpi_count(result):
        if result is None:
            return 0
        return len(result.category_breakdown or {})

    def _inbox_messages(self):
        return self.request.session.get("demo_inbox_messages", DEMO_INBOX_MESSAGES)


class PortalInboxView(PortalAuthMixin, TemplateView):
    template_name = "portal/inbox.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["thread_title"] = "Avery Reader support thread"
        context["thread_messages"] = self.request.session.get("demo_inbox_messages", DEMO_INBOX_MESSAGES)
        context["is_teacher"] = self.request.user.role == CustomUser.Role.TEACHER
        context["is_parent"] = self.request.user.role == CustomUser.Role.GUARDIAN
        context["is_admin"] = self.request.user.role in {CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN}
        return context


class ConfirmPlacementRecommendationView(PortalAuthMixin, View):
    def post(self, request):
        recommendation = (
            PlacementRecommendation.objects.select_related(
                "evidence__child", "recommended_curriculum", "recommended_position"
            )
            .filter(pk=request.POST.get("recommendation_id"), status=PlacementRecommendation.Status.PENDING)
            .first()
        )
        if recommendation is None:
            messages.error(request, "That placement recommendation is no longer pending.")
            return redirect("portal_dashboard")
        if not user_can_evaluate_child(request.user, recommendation.evidence.child):
            messages.error(request, "You are not assigned to this reader's center.")
            return redirect("portal_dashboard")

        final_position = recommendation.recommended_position
        final_position_id = request.POST.get("final_position_id")
        if final_position_id:
            final_position = CurriculumSequence.objects.filter(
                pk=final_position_id,
                curriculum=recommendation.recommended_curriculum,
                is_deleted=False,
            ).first()
        if final_position is None:
            messages.error(request, "Choose a valid final sequence position.")
            return redirect("portal_dashboard")
        try:
            confirm_recommendation(
                recommendation,
                request.user,
                final_position=final_position,
                override_rationale=request.POST.get("override_rationale", "").strip(),
                evidence_considered={"source": "specialist_portal"},
            )
        except ValidationError as error:
            messages.error(request, "; ".join(getattr(error, "messages", [str(error)])))
            return redirect("portal_dashboard")
        messages.success(request, "Placement decision saved with its audit record.")
        return redirect("portal_dashboard")

    def post(self, request, *args, **kwargs):
        body = request.POST.get("message", "").strip()
        if not body:
            messages.error(request, "Write a message before sending.")
            return redirect("portal_inbox")

        sender = request.user.get_full_name() or request.user.email
        inbox_messages = list(request.session.get("demo_inbox_messages", DEMO_INBOX_MESSAGES))
        inbox_messages.append(
            {
                "sender": sender,
                "audience": request.user.role,
                "body": body,
                "sent_at": timezone.localtime().strftime("%b %d, %I:%M %p"),
            }
        )
        request.session["demo_inbox_messages"] = inbox_messages
        messages.success(request, "Message added to the demo thread.")
        return redirect("portal_inbox")


class TeacherAssignmentsView(PortalAuthMixin, TemplateView):
    template_name = "portal/teacher_assignments.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not user_can_manage_instruction(request.user):
            messages.error(request, "Only program administrators can assign teachers.")
            return redirect("portal_dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        teachers = list(program_teachers(user))
        teachers_by_id = {str(teacher.id): teacher for teacher in teachers}
        roster = [reader_assignment(child, teachers_by_id) for child in program_children(user)]
        query = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "all")
        if status not in {"all", "needs", "assigned"}:
            status = "all"
        visible = roster
        if status == "needs":
            visible = [row for row in visible if not row["assigned"]]
        elif status == "assigned":
            visible = [row for row in visible if row["assigned"]]
        if query:
            needle = query.casefold()
            visible = [
                row
                for row in visible
                if needle in str(row["child"]).casefold()
                or needle in row["teacher_name"].casefold()
                or (row["teacher"] and needle in row["teacher"].email.casefold())
            ]
        context.update(
            {
                "teachers": teachers,
                "assignment_choices": roster,
                "roster": visible,
                "reader_count": len(roster),
                "assigned_reader_count": sum(1 for row in roster if row["assigned"]),
                "unassigned_reader_count": sum(1 for row in roster if not row["assigned"]),
                "query": query,
                "status": status,
            }
        )
        return context


class LessonLibraryView(PortalAuthMixin, TemplateView):
    template_name = "portal/lesson_library.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not user_can_manage_instruction(request.user):
            messages.error(request, "Only program administrators can manage the lesson library.")
            return redirect("portal_dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        grade = self.request.GET.get("grade", "").strip()
        lessons = LessonTemplate.objects.filter(is_deleted=False).select_related("skill")
        grade_bands = list(
            lessons.exclude(grade_band="").order_by("grade_band").values_list("grade_band", flat=True).distinct()
        )
        if grade:
            lessons = lessons.filter(grade_band=grade)
        if query:
            lessons = lessons.filter(
                Q(title__icontains=query) | Q(goal__icontains=query) | Q(description__icontains=query)
            )
        assignments = TeacherLessonTemplate.objects.filter(is_deleted=False).select_related("teacher", "template")
        shares_by_template = {}
        for assignment in assignments:
            shares_by_template.setdefault(assignment.template_id, []).append(assignment)
        lesson_rows = []
        for lesson in lessons.order_by("title"):
            shares = shares_by_template.get(lesson.id, [])
            lesson_rows.append(
                {
                    "lesson": lesson,
                    "shares": shares,
                    "activities": lesson.activities if isinstance(lesson.activities, list) else [],
                }
            )
        context.update(
            {
                "teachers": program_teachers(self.request.user),
                "active_lessons": LessonTemplate.objects.filter(is_deleted=False, is_active=True).order_by("title"),
                "lesson_rows": lesson_rows,
                "lesson_count": LessonTemplate.objects.filter(is_deleted=False, is_active=True).count(),
                "shared_lesson_count": assignments.count(),
                "skills": Skill.objects.filter(is_deleted=False).order_by("domain", "code"),
                "query": query,
                "grade": grade,
                "grade_bands": grade_bands,
            }
        )
        return context


class CreateLessonTemplateView(PortalAuthMixin, View):
    def post(self, request):
        if not user_can_manage_instruction(request.user):
            messages.error(request, "Only program administrators can create lessons.")
            return redirect("portal_dashboard")

        title = " ".join(request.POST.get("title", "").split())
        if not title:
            messages.error(request, "Give the lesson a title before saving it.")
            return redirect("portal_lesson_library")
        if len(title) > 255:
            messages.error(request, "Lesson titles need to be 255 characters or fewer.")
            return redirect("portal_lesson_library")

        minutes_raw = request.POST.get("recommended_minutes", "").strip() or "15"
        try:
            recommended_minutes = int(minutes_raw)
        except ValueError:
            recommended_minutes = 0
        if recommended_minutes < 1 or recommended_minutes > 180:
            messages.error(request, "Recommended time needs to be between 1 and 180 minutes.")
            return redirect("portal_lesson_library")

        skill = None
        skill_id = request.POST.get("skill_id", "").strip()
        if skill_id:
            skill = Skill.objects.filter(id=skill_id, is_deleted=False).first()
            if skill is None:
                messages.error(request, "Choose a valid reading skill, or leave that field blank.")
                return redirect("portal_lesson_library")

        lesson = LessonTemplate.objects.create(
            title=title,
            slug=_unique_lesson_slug(title),
            skill=skill,
            grade_band=request.POST.get("grade_band", "").strip()[:64],
            description=request.POST.get("description", "").strip()[:2000],
            goal=request.POST.get("goal", "").strip()[:255],
            recommended_minutes=recommended_minutes,
            activities=_list_field(request.POST.get("activities", "")),
            materials=_list_field(request.POST.get("materials", "")),
            is_active=True,
        )
        messages.success(request, f"Created {lesson.title}. Share it with a teacher when they should use it.")
        return redirect("portal_lesson_library")


class AssignTeacherView(PortalAuthMixin, View):
    def post(self, request):
        if not user_can_manage_instruction(request.user):
            messages.error(request, "Only program administrators can assign teachers.")
            return redirect("portal_dashboard")

        child_id = request.POST.get("child_id")
        teacher_id = request.POST.get("teacher_id")
        child = program_children(request.user).filter(id=child_id).first()
        teacher = program_teachers(request.user).filter(id=teacher_id).first()
        if child is None or teacher is None:
            messages.error(request, "Choose a valid reader and teacher.")
            return redirect("portal_teacher_assignments")

        child.learning_profile = {
            **(child.learning_profile or {}),
            "assigned_teacher_id": teacher.id,
            "assigned_teacher_name": teacher.get_full_name() or teacher.email,
            "assigned_teacher_email": teacher.email,
            "assigned_by_admin_id": request.user.id,
            "assigned_at": timezone.now().isoformat(),
        }
        child.save(update_fields=["learning_profile", "updated_at"])
        messages.success(request, f"Assigned {teacher.get_full_name() or teacher.email} to {child}.")
        return redirect("portal_teacher_assignments")


class AssignTemplateToTeacherView(PortalAuthMixin, View):
    def post(self, request):
        if not user_can_manage_instruction(request.user):
            messages.error(request, "Only program administrators can assign lesson templates.")
            return redirect("portal_dashboard")

        teacher = program_teachers(request.user).filter(id=request.POST.get("teacher_id")).first()
        template = LessonTemplate.objects.filter(
            id=request.POST.get("template_id"),
            is_active=True,
            is_deleted=False,
        ).first()
        if teacher is None or template is None:
            messages.error(request, "Choose a valid teacher and lesson template.")
            return redirect("portal_lesson_library")

        assignment, created = TeacherLessonTemplate.objects.update_or_create(
            teacher=teacher,
            template=template,
            defaults={
                "assigned_by": request.user,
                "notes": request.POST.get("notes", "").strip(),
                "is_deleted": False,
                "deleted_at": None,
            },
        )
        verb = "Assigned" if created else "Updated"
        messages.success(request, f"{verb} {template.title} for {teacher.get_full_name() or teacher.email}.")
        return redirect("portal_lesson_library")


class AssignLessonTemplateToChildView(PortalAuthMixin, View):
    def post(self, request):
        if request.user.role not in {CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN, CustomUser.Role.TEACHER} and not user_can_manage_instruction(request.user):
            messages.error(request, "Only teachers and administrators can assign lessons.")
            return redirect("portal_dashboard")

        child = ChildProfile.objects.filter(id=request.POST.get("child_id"), is_deleted=False).first()
        template = LessonTemplate.objects.filter(
            id=request.POST.get("template_id"),
            is_active=True,
            is_deleted=False,
        ).first()
        if child is None or template is None:
            messages.error(request, "Choose a valid reader and lesson template.")
            return redirect("portal_dashboard")

        if request.user.role == CustomUser.Role.TEACHER:
            if str((child.learning_profile or {}).get("assigned_teacher_id")) != str(request.user.id):
                messages.error(request, "That reader is not assigned to your teacher workspace.")
                return redirect("portal_dashboard")
            has_template = TeacherLessonTemplate.objects.filter(
                teacher=request.user,
                template=template,
                is_deleted=False,
            ).exists()
            if not has_template:
                messages.error(request, "That lesson template has not been assigned to you by an administrator.")
                return redirect("portal_dashboard")

        due_date_value = parse_date(request.POST.get("due_date", "").strip()) if request.POST.get("due_date") else None
        existing = ChildLessonAssignment.objects.filter(
            child=child,
            template=template,
            status__in=[ChildLessonAssignment.Status.ASSIGNED, ChildLessonAssignment.Status.IN_PROGRESS],
            is_deleted=False,
        ).first()
        if existing:
            existing.assigned_by = request.user
            existing.due_date = due_date_value
            existing.teacher_notes = request.POST.get("teacher_notes", "").strip()
            existing.save(update_fields=["assigned_by", "due_date", "teacher_notes", "updated_at"])
            messages.success(request, f"Updated {template.title} for {child}.")
        else:
            ChildLessonAssignment.objects.create(
                child=child,
                template=template,
                assigned_by=request.user,
                due_date=due_date_value,
                teacher_notes=request.POST.get("teacher_notes", "").strip(),
            )
            messages.success(request, f"Assigned {template.title} to {child}.")
        return redirect("portal_dashboard")


class CreatePortalUserView(PortalAuthMixin, View):
    def post(self, request):
        from apps.users.onboarding_views import manage_users

        return manage_users(request)
