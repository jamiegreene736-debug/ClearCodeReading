from django.contrib import admin
from django.conf import settings
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from django.views.generic import TemplateView
from django.views.static import serve

from apps.assessments.views import assessment_audio, assessment_audio_status
from apps.crm.views import WebsiteSignupView
from apps.users.portal_views import (
    AssignLessonTemplateToChildView,
    AssignTeacherView,
    AssignTemplateToTeacherView,
    CreatePortalUserView,
    DemoLoginView,
    PortalDashboardView,
    PortalInboxView,
    PortalLoginView,
)

urlpatterns = [
    path("", TemplateView.as_view(template_name="index.html"), name="marketing_home"),
    path("assessment/", TemplateView.as_view(template_name="assessment.html"), name="reading_assessment"),
    path("login/", PortalLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="/"), name="logout"),
    path("dashboard/", PortalDashboardView.as_view(), name="portal_dashboard"),
    path("inbox/", PortalInboxView.as_view(), name="portal_inbox"),
    path("crm/signup/", WebsiteSignupView.as_view(), name="crm_signup"),
    path("assign-teacher/", AssignTeacherView.as_view(), name="assign_teacher"),
    path("portal/templates/assign-teacher/", AssignTemplateToTeacherView.as_view(), name="portal_assign_template_to_teacher"),
    path("portal/lessons/assign-child/", AssignLessonTemplateToChildView.as_view(), name="portal_assign_lesson_to_child"),
    path("portal/users/create/", CreatePortalUserView.as_view(), name="portal_create_user"),
    path("demo-login/<str:role>/", DemoLoginView.as_view(), name="demo_login"),
    path("assessment-audio/status/", assessment_audio_status, name="assessment_audio_status"),
    path("assessment-audio/<str:key>.mp3", assessment_audio, name="assessment_audio"),
    path("assets/<path:path>", serve, {"document_root": settings.BASE_DIR / "marketing-website" / "assets"}, name="marketing_assets"),
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
]
