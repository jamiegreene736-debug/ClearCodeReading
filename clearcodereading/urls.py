from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from django.views.generic import TemplateView
from django.views.static import serve

from apps.assessments.views import assessment_audio, assessment_audio_status
from apps.crm.views import (
    CrmCompanyDetailView,
    CrmCompanyListView,
    CrmContactDetailView,
    CrmContactListView,
    CrmContactUpdateView,
    CrmDealCreateView,
    CrmDealListView,
    CrmDealStageUpdateView,
    CrmNoteCreateView,
    CrmTaskCompleteView,
    CrmTaskCreateView,
    CrmTriageListView,
    CrmTriageResolveView,
    NewsletterSignupView,
    NewsletterUnsubscribeView,
    WebsiteSignupView,
)
from apps.sessions.views import RapidSessionLogView
from apps.users.portal_views import (
    AssignLessonTemplateToChildView,
    AssignTeacherView,
    AssignTemplateToTeacherView,
    CreatePortalUserView,
    ConfirmPlacementRecommendationView,
    DemoLoginView,
    PortalDashboardView,
    PortalInboxView,
    PortalLoginView,
)

urlpatterns = [
    path("", TemplateView.as_view(template_name="index.html"), name="marketing_home"),
    path("about/", TemplateView.as_view(template_name="about.html"), name="marketing_about"),
    path(
        "favicon.ico",
        serve,
        {
            "path": "logo/favicon.ico",
            "document_root": settings.BASE_DIR / "marketing-website" / "assets",
        },
        name="favicon",
    ),
    path("how-it-works/", TemplateView.as_view(template_name="how-it-works.html"), name="marketing_how_it_works"),
    path("families/", TemplateView.as_view(template_name="families.html"), name="marketing_families"),
    path("foundation/", TemplateView.as_view(template_name="foundation.html"), name="marketing_foundation"),
    path("careers/", TemplateView.as_view(template_name="careers.html"), name="marketing_careers"),
    path("contact/", TemplateView.as_view(template_name="contact.html"), name="marketing_contact"),
    path("privacy/", TemplateView.as_view(template_name="privacy.html"), name="marketing_privacy"),
    path("approach/", TemplateView.as_view(template_name="approach.html"), name="marketing_approach"),
    path("assessment/", TemplateView.as_view(template_name="assessment.html"), name="reading_assessment"),
    path("blog/", include("apps.blog.urls")),
    path("login/", PortalLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="/"), name="logout"),
    path("dashboard/", PortalDashboardView.as_view(), name="portal_dashboard"),
    path("inbox/", PortalInboxView.as_view(), name="portal_inbox"),
    path("portal/sessions/rapid-log/", RapidSessionLogView.as_view(), name="rapid_session_log"),
    path("crm/", CrmContactListView.as_view(), name="crm_contact_list"),
    path("crm/companies/", CrmCompanyListView.as_view(), name="crm_company_list"),
    path("crm/companies/<int:pk>/", CrmCompanyDetailView.as_view(), name="crm_company_detail"),
    path("crm/deals/", CrmDealListView.as_view(), name="crm_deal_list"),
    path("crm/deals/<int:pk>/stage/", CrmDealStageUpdateView.as_view(), name="crm_deal_stage_update"),
    path("crm/triage/", CrmTriageListView.as_view(), name="crm_triage_list"),
    path("crm/triage/<int:pk>/resolve/", CrmTriageResolveView.as_view(), name="crm_triage_resolve"),
    path("crm/contacts/<int:pk>/", CrmContactDetailView.as_view(), name="crm_contact_detail"),
    path("crm/contacts/<int:pk>/update/", CrmContactUpdateView.as_view(), name="crm_contact_update"),
    path("crm/contacts/<int:pk>/deals/", CrmDealCreateView.as_view(), name="crm_deal_create"),
    path("crm/contacts/<int:pk>/notes/", CrmNoteCreateView.as_view(), name="crm_note_create"),
    path("crm/contacts/<int:pk>/tasks/", CrmTaskCreateView.as_view(), name="crm_task_create"),
    path(
        "crm/contacts/<int:pk>/tasks/<int:activity_id>/complete/",
        CrmTaskCompleteView.as_view(),
        name="crm_task_complete",
    ),
    path("crm/signup/", WebsiteSignupView.as_view(), name="crm_signup"),
    path("newsletter/subscribe/", NewsletterSignupView.as_view(), name="newsletter_signup"),
    path(
        "newsletter/unsubscribe/<str:token>/",
        NewsletterUnsubscribeView.as_view(),
        name="newsletter_unsubscribe",
    ),
    path("assign-teacher/", AssignTeacherView.as_view(), name="assign_teacher"),
    path("portal/templates/assign-teacher/", AssignTemplateToTeacherView.as_view(), name="portal_assign_template_to_teacher"),
    path("portal/lessons/assign-child/", AssignLessonTemplateToChildView.as_view(), name="portal_assign_lesson_to_child"),
    path("portal/users/create/", CreatePortalUserView.as_view(), name="portal_create_user"),
    path(
        "portal/placements/confirm/",
        ConfirmPlacementRecommendationView.as_view(),
        name="portal_confirm_placement",
    ),
    path("demo-login/<str:role>/", DemoLoginView.as_view(), name="demo_login"),
    path("assessment-audio/status/", assessment_audio_status, name="assessment_audio_status"),
    path("assessment-audio/<str:key>.mp3", assessment_audio, name="assessment_audio"),
    path("assets/<path:path>", serve, {"document_root": settings.BASE_DIR / "marketing-website" / "assets"}, name="marketing_assets"),
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
