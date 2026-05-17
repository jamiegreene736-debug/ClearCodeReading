from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView

app_name = "api"

urlpatterns = [
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("v1/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("v1/auth/token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("v1/", include("apps.core.urls")),
    path("v1/", include("apps.users.urls")),
    path("v1/", include("apps.schools.urls")),
    path("v1/", include("apps.assessments.urls")),
    path("v1/", include("apps.curriculum.urls")),
    path("v1/", include("apps.progress.urls")),
    path("v1/", include("apps.crm.urls")),
]
