from django.contrib import admin
from django.conf import settings
from django.urls import include, path
from django.views.generic import TemplateView
from django.views.static import serve

urlpatterns = [
    path("", TemplateView.as_view(template_name="index.html"), name="marketing_home"),
    path("assets/<path:path>", serve, {"document_root": settings.BASE_DIR / "marketing-website" / "assets"}, name="marketing_assets"),
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
]
