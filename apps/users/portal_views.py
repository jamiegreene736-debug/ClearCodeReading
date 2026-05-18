from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.views import LoginView
from django.db.models import Avg
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.generic import TemplateView, View

from apps.assessments.models import Assessment, AssessmentResult
from apps.crm.models import Lead
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


class PortalLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True
    next_page = reverse_lazy("portal_dashboard")

    def form_valid(self, form):
        messages.success(self.request, "Welcome back to Clear Code Reading.")
        return super().form_valid(form)


class DemoLoginView(View):
    def post(self, request, role):
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["is_admin"] = user.role in {CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN}
        context["is_parent"] = user.role == CustomUser.Role.GUARDIAN
        context["is_teacher"] = user.role == CustomUser.Role.TEACHER

        if context["is_parent"]:
            relationships = GuardianRelationship.objects.filter(
                guardian=user,
                is_deleted=False,
                child__is_deleted=False,
            ).select_related("child")
            children = [relationship.child for relationship in relationships]
        else:
            children = list(ChildProfile.objects.filter(is_deleted=False).order_by("last_name", "first_name")[:12])

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
        recent_leads = Lead.objects.none()
        lead_count = 0
        new_lead_count = 0
        if context["is_admin"]:
            lead_queryset = Lead.objects.filter(is_deleted=False).select_related("assigned_to", "linked_user")
            recent_leads = lead_queryset.order_by("-created_at")[:8]
            lead_count = lead_queryset.count()
            new_lead_count = lead_queryset.filter(status=Lead.Status.NEW).count()

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
                "kpi_count": self._kpi_count(latest_results.first()),
                "teachers": CustomUser.objects.filter(role=CustomUser.Role.TEACHER, is_active=True, is_deleted=False),
                "recent_leads": recent_leads,
                "lead_count": lead_count,
                "new_lead_count": new_lead_count,
                "inbox_messages": self._inbox_messages()[-2:],
            }
        )
        return context

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


class AssignTeacherView(PortalAuthMixin, View):
    def post(self, request):
        if request.user.role not in {CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN}:
            messages.error(request, "Only program administrators can assign teachers.")
            return redirect("portal_dashboard")

        child_id = request.POST.get("child_id")
        teacher_id = request.POST.get("teacher_id")
        child = ChildProfile.objects.filter(id=child_id, is_deleted=False).first()
        teacher = CustomUser.objects.filter(id=teacher_id, role=CustomUser.Role.TEACHER, is_active=True, is_deleted=False).first()
        if child is None or teacher is None:
            messages.error(request, "Choose a valid reader and teacher.")
            return redirect("portal_dashboard")

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
        return redirect("portal_dashboard")


class CreatePortalUserView(PortalAuthMixin, View):
    def post(self, request):
        if request.user.role not in {CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN}:
            messages.error(request, "Only program administrators can create accounts.")
            return redirect("portal_dashboard")

        role = request.POST.get("role")
        if role not in {CustomUser.Role.GUARDIAN, CustomUser.Role.TEACHER}:
            messages.error(request, "Create either a parent or teacher account.")
            return redirect("portal_dashboard")

        email = request.POST.get("email", "").strip().lower()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        phone_number = request.POST.get("phone_number", "").strip()
        raw_password = request.POST.get("password", "").strip() or self._temporary_password()
        if not email:
            messages.error(request, "Email is required to create an account.")
            return redirect("portal_dashboard")
        if CustomUser.objects.filter(email=email, is_deleted=False).exists():
            messages.error(request, f"An account already exists for {email}.")
            return redirect("portal_dashboard")

        user = CustomUser.objects.create_user(
            username=self._unique_username(email),
            email=email,
            password=raw_password,
            first_name=first_name,
            last_name=last_name,
            role=role,
            phone_number=phone_number,
            is_active=True,
            metadata={
                "created_from_portal": True,
                "created_by_admin_id": request.user.id,
                "created_at": timezone.now().isoformat(),
            },
        )

        Lead.objects.filter(contact_email=email, is_deleted=False).update(linked_user=user, updated_at=timezone.now())
        messages.success(
            request,
            f"Created {user.get_role_display()} account for {user.get_full_name() or user.email}. Temporary password: {raw_password}",
        )
        return redirect("portal_dashboard")

    @staticmethod
    def _temporary_password():
        return f"ClearCode-{get_random_string(10)}!"

    @staticmethod
    def _unique_username(email):
        base = email.split("@", 1)[0].replace("+", "-")[:120] or "user"
        username = base
        counter = 1
        while CustomUser.objects.filter(username=username).exists():
            counter += 1
            username = f"{base}-{counter}"
        return username
