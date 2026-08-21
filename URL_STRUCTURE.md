# Clear Code Reading URL Structure

## Endpoint Map

- `/`
- `/how-it-works/`
- `/families/`
- `/approach/`
- `/privacy/`
- `/contact/`
- `/assessment/`
- `/blog/`
- `/blog/<slug>/`
- `/login/`
- `/crm/signup/`
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
- `/api/v1/provider-availability/`
- `/api/v1/schedule-proposals/`
- `/api/v1/schedule-proposals/generate/`
- `/api/v1/schedule-proposals/<id>/approve/`
- `/api/v1/schedule-proposals/<id>/reject/`
- `/api/v1/schedule-bookings/`
- `/api/v1/schedule-bookings/recommendations/?center=<id>`
- `/api/v1/schedule-bookings/operations-metrics/?center=<id>`
- `/api/v1/schedule-bookings/<id>/approve/`
- `/api/v1/schedule-bookings/<id>/sync/`
- `/api/v1/schedule-bookings/<id>/force-sync/`
- `/api/v1/schedule-bookings/reconcile-inbound/`
- `/api/v1/waitlist/`
- `/api/v1/mastery-records/`
- `/api/v1/leads/`
- `/api/v1/leads/<id>/qualify/`
- `/api/v1/leads/<id>/convert/`
- `/api/v1/opportunities/`
- `/api/v1/opportunities/<id>/advance/`

## Public marketing routes

| Route | Purpose |
|---|---|
| `/` | Intervention Intelligence Platform homepage |
| `/how-it-works/` | Placement, sessions, parent dashboard, and decision-support flow |
| `/families/` | Parent journey, dashboard visibility, and home-practice expectations |
| `/approach/` | Structured-literacy, one-methodology, specialist-led, education-only stance |
| `/privacy/` | Plain-language FERPA-oriented privacy and consent overview |
| `/contact/` | Primary family consultation form |
| `/assessment/` | Optional secondary reading survey |
| `/blog/` | Published reading insights and resources |
| `/blog/<slug>/` | Public article detail; drafts and scheduled posts return 404 |
| `/crm/signup/` | POST-only CRM lead capture used by public inquiry forms |

## `clearcodereading/urls.py`
```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
]

```

## Phase 1 routes

All routes below are under `/api/v1/` and require an authenticated evaluator with access
to the record's center.

| Route | Methods | Purpose |
|---|---|---|
| `curricula/` | GET | Available center-scoped methodology versions |
| `curriculum-positions/` | GET | Frozen graph positions; filter by `curriculum` |
| `placement-evidence/` | GET, POST, PATCH | Structured placement entry/import |
| `placement-evidence/<id>/recommend/` | POST | Generate/re-run deterministic mapping |
| `placement-recommendations/` | GET | Pending and historical recommendations |
| `placement-recommendations/<id>/confirm/` | POST | Confirm or label an override |
| `placement-recommendations/grouping-suggestions/` | GET | Skill-based grouping feed |
| `student-placements/` | GET | Active and historical placements |
| `sessions/` | GET, POST, PATCH | Structured specialist session capture |
| `sessions/defaults/?child=<id>` | GET | Active-placement logging defaults |
| `sessions/logging-metrics/` | GET | Same-day logging rate |

The specialist portal decision endpoint is
`POST /portal/placements/confirm/`. Django admin remains available at `/admin/` for
placement review, evidence entry, session logging, and revision inspection.

## Scheduling optimizer routes

All scheduling records are center-scoped. Proposal generation is available to center
owners, admins, and specialists; approval, rejection, inbound reconciliation, and
operations metrics require center owner/admin access.

| Route | Methods | Purpose |
|---|---|---|
| `schedule-proposals/` | GET | Persistent advisory proposals visible within accessible centers |
| `schedule-proposals/generate/` | POST | Generate date-range, skill-compatible proposals and proposed child bookings |
| `schedule-proposals/<id>/approve/` | POST | Atomically revalidate consent/placement and approve the whole group |
| `schedule-proposals/<id>/reject/` | POST | Reject the group and cancel its proposed bookings |
| `schedule-bookings/<id>/force-sync/` | POST | Push or retry an approved booking through the configured adapter |
| `schedule-bookings/reconcile-inbound/` | POST | Pull time/status/cancellation changes for known external IDs |
| `schedule-bookings/operations-metrics/` | GET | Capacity, utilization, waitlist, concentration, and expansion signals |

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
