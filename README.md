# Clear Code Reading

Clear Code Reading is a tenant-aware Django 5.1 API for school-based reading assessment, human evaluator review, COPPA consent, curriculum recommendations, learner progress tracking, and CRM onboarding.

## Stack

- Django 5.1 and Django REST Framework
- PostgreSQL with `django-tenants`
- Redis and Celery for async notifications
- SimpleJWT authentication
- drf-spectacular OpenAPI docs
- django-guardian for object permission support

## Quick Start

Build and run everything:

```bash
docker compose up --build
```

The compose file uses `.env.example` by default so the project can boot from a fresh clone. For local secrets, copy it and adjust values:

```bash
cp .env.example .env
```

Run shared tenant migrations:

```bash
docker compose run --rm web python manage.py migrate_schemas --shared
```

Create an admin user:

```bash
docker compose run --rm web python manage.py createsuperuser
```

Useful URLs:

- Admin: `http://localhost:8000/admin/`
- Swagger docs: `http://localhost:8000/api/docs/`
- ReDoc: `http://localhost:8000/api/redoc/`
- OpenAPI schema: `http://localhost:8000/api/schema/`
- Health check: `http://localhost:8000/api/v1/health/`

## API Overview

Authentication:

- `POST /api/v1/auth/token/`
- `POST /api/v1/auth/token/refresh/`
- `POST /api/v1/auth/token/verify/`

Users and consent:

- `/api/v1/users/`
- `/api/v1/users/register-parent-child/`
- `/api/v1/children/`
- `/api/v1/guardian-relationships/`
- `/api/v1/guardian-relationships/<id>/grant-consent/`
- `/api/v1/guardian-relationships/<id>/revoke-consent/`
- `/api/v1/consents/`

Schools:

- `/api/v1/schools/`
- `/api/v1/schools/onboard/`
- `/api/v1/schools/<id>/invite/`
- `/api/v1/memberships/`

Assessments:

- `/api/v1/assessments/`
- `/api/v1/assessments/<id>/submit/`
- `/api/v1/assessments/<id>/review/`
- `/api/v1/assessments/<id>/transition/`

Curriculum and progress:

- `/api/v1/skills/`
- `/api/v1/lessons/`
- `/api/v1/lessons/personalized/`
- `/api/v1/teaching-aids/`
- `/api/v1/progress/`
- `/api/v1/progress/dashboard/`
- `/api/v1/mastery-records/`

CRM:

- `/api/v1/leads/`
- `/api/v1/leads/<id>/qualify/`
- `/api/v1/leads/<id>/convert/`
- `/api/v1/opportunities/`
- `/api/v1/opportunities/<id>/advance/`

## Assessment Workflow

Clear Code Reading uses a human-in-the-loop assessment path:

1. An assessment starts as `pending`.
2. Digital submission moves it to `human_review`.
3. Evaluators review and complete it.
4. Completion updates progress records and queues parent notifications.

Celery tasks notify evaluators when human review is needed and send parent progress reports after review.

## COPPA Notes

Clear Code Reading treats child learning data as consent-gated.

- Parent/guardian registration creates a child profile and guardian relationship.
- Consent logs track consent type, status, version, source, IP, user agent, and expiry.
- Assessment, progress, mastery, and personalized lesson flows enforce active COPPA consent.
- Revoked or expired consent blocks sensitive child learning updates.
- Audit logs capture consent and assessment-status events for compliance review.

Production deployments should connect real email/SMS providers, keep `PUBLIC_APP_URL` accurate, store secrets outside git, and review data-retention rules with counsel.

## Async Workers

Start workers through Docker Compose:

```bash
docker compose up --build celery celery-beat
```

Notification code lives in `apps/notifications/`:

- consent request email/SMS
- evaluator human-review notifications
- parent progress reports
- signal handlers for consent and assessment status changes

## Tests

Run the smoke and workflow tests:

```bash
docker compose run --rm web python manage.py test apps.users apps.schools apps.assessments apps.curriculum apps.progress apps.crm apps.notifications
```

The included tests cover model constants, serializer behavior, notification service helpers, and the assessment status workflow.

## Project Layout

```text
clearcodereading/
apps/
  api/
  assessments/
  core/
  crm/
  curriculum/
  notifications/
  progress/
  schools/
  tenants/
  users/
scripts/
```
