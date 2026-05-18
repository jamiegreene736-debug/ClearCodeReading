from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.views import LoginView
from django.db.models import Avg, Count
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView, View

from apps.assessments.models import Assessment, AssessmentResult
from apps.users.models import ChildProfile, CustomUser, GuardianRelationship


DEMO_LOGINS = {
    "parent": "parent@clearcodereading.com",
    "teacher": "teacher@clearcodereading.com",
}


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


class PortalDashboardView(TemplateView):
    template_name = "portal/dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse_lazy('login')}?next={request.path}")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["is_parent"] = user.role == CustomUser.Role.GUARDIAN
        context["is_teacher"] = user.role in {
            CustomUser.Role.TEACHER,
            CustomUser.Role.SCHOOL_ADMIN,
            CustomUser.Role.SUPER_ADMIN,
        }

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
            }
        )
        return context

    @staticmethod
    def _kpi_count(result):
        if result is None:
            return 0
        return len(result.category_breakdown or {})
