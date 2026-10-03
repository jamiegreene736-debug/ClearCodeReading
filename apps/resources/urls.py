from __future__ import annotations

from django.contrib.auth.views import LoginView
from django.urls import path, reverse_lazy

from apps.resources import views
from apps.resources.access import public_site
from apps.resources.onboarding import setup_staff

app_name = "resources"
class StaffLoginView(LoginView):
    template_name = "resources/signin.html"
    next_page = reverse_lazy("resources:manager")

    def post(self, request, *args, **kwargs):
        from apps.core.bot_protection import redirect_if_blocked

        blocked = redirect_if_blocked(request, "staff-login", request.path)
        if blocked is not None:
            return blocked
        return super().post(request, *args, **kwargs)


urlpatterns = [
    path("staff-setup/<str:token>/", setup_staff, name="setup_staff"),
    path(
        "staff-sign-in/",
        public_site(StaffLoginView.as_view()),
        name="sign_in",
    ),
    path("manage/", views.manager, name="manager"),
    path("manage/add/", views.add, name="add"),
    path("manage/files/", views.media_library, name="media"),
    path("manage/<uuid:pk>/", views.edit, name="edit"),
    path("manage/<uuid:pk>/action/", views.action, name="action"),
    path("manage/<uuid:pk>/preview/", views.preview, name="preview"),
    path(
        "manage/<uuid:pk>/preview/<str:role>/",
        views.preview_asset,
        name="preview_asset",
    ),
    path("<uuid:pk>/asset/<str:role>/", views.asset, name="asset"),
    path("<uuid:pk>/<slug:slug>/", views.detail, name="detail"),
]
