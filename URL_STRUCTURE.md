# Clear Code Reading URL Structure

## Endpoint Map

- `/admin/`
- `/api/schema/`
- `/api/docs/`
- `/api/redoc/`
- `/api/v1/auth/token/`
- `/api/v1/auth/token/refresh/`
- `/api/v1/auth/token/verify/`
- `/api/v1/health/`
- `/api/v1/users/`
- `/api/v1/users/register-parent-child/`
- `/api/v1/profiles/`
- `/api/v1/children/`
- `/api/v1/guardian-relationships/`
- `/api/v1/guardian-relationships/<id>/grant-consent/`
- `/api/v1/guardian-relationships/<id>/revoke-consent/`
- `/api/v1/consents/`
- `/api/v1/audit-logs/`
- `/api/v1/schools/`
- `/api/v1/schools/onboard/`
- `/api/v1/schools/<id>/invite/`
- `/api/v1/memberships/`
- `/api/v1/assessments/`
- `/api/v1/assessments/<id>/submit/`
- `/api/v1/assessments/<id>/review/`
- `/api/v1/assessments/<id>/transition/`
- `/api/v1/skills/`
- `/api/v1/lessons/`
- `/api/v1/lessons/personalized/`
- `/api/v1/teaching-aids/`
- `/api/v1/progress/`
- `/api/v1/progress/dashboard/`
- `/api/v1/mastery-records/`
- `/api/v1/leads/`
- `/api/v1/leads/<id>/qualify/`
- `/api/v1/leads/<id>/convert/`
- `/api/v1/opportunities/`
- `/api/v1/opportunities/<id>/advance/`

## `clearcodereading/urls.py`
```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
]

```

## `apps/api/urls.py`
```python
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

```

## `apps/core/urls.py`
```python
from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({"status": "ok"})


urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health"),
]

```

## `apps/users/urls.py`
```python
from rest_framework.routers import DefaultRouter

from apps.users.views import (
    AuditLogViewSet,
    ChildProfileViewSet,
    ConsentLogViewSet,
    GuardianRelationshipViewSet,
    ProfileViewSet,
    UserViewSet,
)

app_name = "users"

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("profiles", ProfileViewSet, basename="profile")
router.register("children", ChildProfileViewSet, basename="child")
router.register("guardian-relationships", GuardianRelationshipViewSet, basename="guardian-relationship")
router.register("consents", ConsentLogViewSet, basename="consent")
router.register("audit-logs", AuditLogViewSet, basename="audit-log")

urlpatterns = router.urls

```

## `apps/schools/urls.py`
```python
from rest_framework.routers import DefaultRouter

from apps.schools.views import SchoolMembershipViewSet, SchoolViewSet

app_name = "schools"

router = DefaultRouter()
router.register("schools", SchoolViewSet, basename="school")
router.register("memberships", SchoolMembershipViewSet, basename="school-membership")

urlpatterns = router.urls

```

## `apps/assessments/urls.py`
```python
from rest_framework.routers import DefaultRouter

from apps.assessments.views import AssessmentViewSet

app_name = "assessments"

router = DefaultRouter()
router.register("assessments", AssessmentViewSet, basename="assessment")

urlpatterns = router.urls

```

## `apps/curriculum/urls.py`
```python
from rest_framework.routers import DefaultRouter

from apps.curriculum.views import LessonViewSet, SkillViewSet, TeachingAidViewSet

app_name = "curriculum"

router = DefaultRouter()
router.register("skills", SkillViewSet, basename="skill")
router.register("lessons", LessonViewSet, basename="lesson")
router.register("teaching-aids", TeachingAidViewSet, basename="teaching-aid")

urlpatterns = router.urls

```

## `apps/progress/urls.py`
```python
from rest_framework.routers import DefaultRouter

from apps.progress.views import MasteryRecordViewSet, ProgressViewSet

app_name = "progress"

router = DefaultRouter()
router.register("progress", ProgressViewSet, basename="progress")
router.register("mastery-records", MasteryRecordViewSet, basename="mastery-record")

urlpatterns = router.urls

```

## `apps/crm/urls.py`
```python
from rest_framework.routers import DefaultRouter

from apps.crm.views import LeadViewSet, OpportunityViewSet

app_name = "crm"

router = DefaultRouter()
router.register("leads", LeadViewSet, basename="lead")
router.register("opportunities", OpportunityViewSet, basename="opportunity")

urlpatterns = router.urls

```
