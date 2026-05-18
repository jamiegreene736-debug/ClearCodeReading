from django.contrib import admin
from django.conf import settings
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from django.views.generic import TemplateView
from django.views.static import serve

from apps.users.portal_views import DemoLoginView, PortalDashboardView, PortalLoginView

urlpatterns = [
    path("", TemplateView.as_view(template_name="index.html"), name="marketing_home"),
    path("assessment/", TemplateView.as_view(template_name="assessment.html"), name="reading_assessment"),
    path("login/", PortalLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="/"), name="logout"),
    path("dashboard/", PortalDashboardView.as_view(), name="portal_dashboard"),
    path("demo-login/<str:role>/", DemoLoginView.as_view(), name="demo_login"),
    path("assets/<path:path>", serve, {"document_root": settings.BASE_DIR / "marketing-website" / "assets"}, name="marketing_assets"),
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
]
