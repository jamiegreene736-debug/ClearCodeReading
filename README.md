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
- Browser survey demo: `http://localhost:8000/assessment/`
- Swagger docs: `http://localhost:8000/api/docs/`
- ReDoc: `http://localhost:8000/api/redoc/`
- OpenAPI schema: `http://localhost:8000/api/schema/`
- Health check: `http://localhost:8000/api/v1/health/`

Demo credentials:

- Admin: `admin@clearcodereading.com` / `ClearCodeDemo!2026`
- Teacher: `teacher@clearcodereading.com` / `ClearCodeDemo!2026`
- Parent: `parent@clearcodereading.com` / `ClearCodeDemo!2026`

Create or refresh demo credentials:

```bash
docker compose run --rm web python manage.py seed_demo_login
```

On Railway, the deploy start command runs migrations, seeds the Reading Survey question bank, and creates these demo credentials automatically.

If `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` are set in Railway, predeploy also generates any missing cached assessment audio into PostgreSQL.

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
- `POST /api/v1/assessments/start-survey/`
- `GET /api/v1/assessments/<id>/questions/?section=phonics`
- `POST /api/v1/assessments/<id>/answer/`
- `POST /api/v1/assessments/<id>/complete/`
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

## Reading Survey

The Reading Survey is the child-friendly digital assessment that starts the Clear Code Reading placement flow. It creates an `Assessment`, serves progressive questions, saves answers as `ChildAssessmentResponse` records, computes a reading-age estimate, and moves the assessment into `human_review` so a real evaluator can add notes before final placement.

The current browser-based assessment experience is served at `/assessment/`. It is a standalone marketing/demo survey. The database-backed assessment workflow is available through the `/api/v1/assessments/` endpoints below.

Seed the starter question bank:

```bash
docker compose run --rm web python manage.py seed_reading_survey_questions
```

The command seeds 14 starter questions and can be run repeatedly without creating duplicates.

### ElevenLabs assessment audio

The browser assessment can use cached ElevenLabs MP3 files instead of robotic browser speech. This keeps free-tier usage under control because audio is generated once, stored in PostgreSQL, and served by Django from `/assessment-audio/<key>.mp3`.

On Railway, set these variables on the web service:

- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID`

Optional:

- `ELEVENLABS_FALLBACK_VOICE_ID` if the primary voice is rejected by the API. Defaults to ElevenLabs' premade Rachel voice.

The app trims common Railway copy/paste mistakes such as surrounding quotes, `Bearer ` prefixes, or accidentally pasting `ELEVENLABS_API_KEY=...` as the value. If `/assessment-audio/intro.mp3` returns `x-assessment-audio-reason: api_key_rejected`, replace the Railway `ELEVENLABS_API_KEY` value with a fresh key copied directly from ElevenLabs.

On the next deploy, `scripts/predeploy.sh` runs `python manage.py generate_assessment_audio --no-fail` automatically. The command skips audio that already exists in the database, so future deploys should not spend credits again unless you intentionally delete records or run with `--force`. If predeploy skips or misses a clip, the `/assessment-audio/<key>.mp3` endpoint will generate that one missing clip the first time it is requested, save it to PostgreSQL, and reuse the cached MP3 after that. If ElevenLabs rejects a key, voice id, or quota, deploy will continue and the logs will show which clips failed.

For local/manual generation:

```bash
export ELEVENLABS_API_KEY=your-key
export ELEVENLABS_VOICE_ID=your-voice-id
python manage.py generate_assessment_audio
```

Useful options:

- `--dry-run` shows the files that would be generated without calling ElevenLabs.
- `--no-fail` logs ElevenLabs errors without failing deploy.
- `--force` regenerates existing database audio. Avoid this on the free tier unless you intentionally want to spend credits again.
- `--model-id` defaults to `eleven_multilingual_v2`.
- `--output-format` defaults to `mp3_44100_128`.

The frontend never calls ElevenLabs from the child’s browser. If database audio is missing and the server cannot generate it because the keys are missing, invalid, or over quota, the assessment falls back to browser speech.

Check whether the database has cached audio:

```bash
python manage.py check_assessment_audio
```

Reading Survey endpoints:

- `POST /api/v1/assessments/start-survey/` creates a survey assessment and returns the first section of questions.
- `GET /api/v1/assessments/<id>/questions/?section=phonics` returns questions, optionally filtered by KPI section.
- `POST /api/v1/assessments/<id>/answer/` saves one answer or a batch of answers.
- `POST /api/v1/assessments/<id>/complete/` computes the final report and queues evaluator review.

Measured KPIs:

- Phonemic awareness: beginning sounds, sound counting, and spoken-word sound awareness.
- Letter sounds: letter-sound mapping and early alphabetic principle.
- Phonics / decoding: CVC words, blends, digraphs, rhyming, and sound-symbol decoding.
- Advanced decoding: vowel teams, more complex spelling patterns, and flexible decoding.
- Sight words: recognition of high-frequency words.
- Fluency: accuracy, pacing, and expression on short read-aloud prompts.
- Vocabulary: child-friendly word meaning and context understanding.
- Comprehension: prediction, inference, retell, and main-event understanding.
- Writing readiness: complete-thought sentence production and early written/oral expression.
- Reading confidence: self-reported comfort and willingness to try.

Scoring:

- Each response earns a numeric `score_value`, usually `0.00`, `0.50`, or `1.00`.
- Category scores are converted to percentages.
- Overall score is weighted, with extra emphasis on phonics, fluency, and comprehension.
- Reading Age is mapped from the weighted score into a `4.0` to `11.0` year range.
- The final child-facing message uses this format: `You are reading at an X-year-old level`.
- Strengths are categories scoring `75%` or higher.
- Growth areas are categories below `55%`.

Example start request:

```bash
curl -X POST http://localhost:8000/api/v1/assessments/start-survey/ \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{"child": 1, "first_section": "phonics", "question_limit": 5}'
```

Example answer request:

```bash
curl -X POST http://localhost:8000/api/v1/assessments/1/answer/ \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "answers": [
      {"question": 5, "selected_option": 14, "time_taken": 8},
      {"question": 6, "selected_option": 16, "time_taken": 6}
    ]
  }'
```

Example final report JSON:

```json
{
  "assessment": {
    "id": 1,
    "status": "human_review",
    "title": "Reading Survey for Maya",
    "overall_score": 78,
    "reading_age": "9.5",
    "survey_completed_at": "2026-05-17T23:15:00Z"
  },
  "result": {
    "id": 1,
    "assessment": 1,
    "final_scores": {
      "overall_score": 78,
      "response_count": 14,
      "final_message": "You are reading at an 9.5-year-old level"
    },
    "reading_age": "9.5",
    "grade_equivalent": "Grade 4",
    "category_breakdown": {
      "phonics": {
        "label": "Phonics / decoding",
        "earned": 2.0,
        "possible": 2.0,
        "score": 100,
        "responses": 2
      },
      "comprehension": {
        "label": "Comprehension",
        "earned": 1.0,
        "possible": 2.0,
        "score": 50,
        "responses": 2
      }
    },
    "strengths": ["Phonics / decoding", "Sight words", "Vocabulary"],
    "growth_areas": ["Comprehension", "Fluency"],
    "teacher_summary": "Digital survey score: 78%. Estimated reading age: 9.5. Strengths: Phonics / decoding, Sight words, Vocabulary. Priority growth areas: Comprehension, Fluency. Human evaluator review is recommended before final placement.",
    "final_message": "You are reading at an 9.5-year-old level"
  },
  "final_message": "You are reading at an 9.5-year-old level"
}
```

Full flow test checklist:

1. Start services: `docker compose up --build`.
2. Run migrations: `docker compose run --rm web python manage.py migrate_schemas --shared`.
3. Create an admin user: `docker compose run --rm web python manage.py createsuperuser`.
4. Seed survey questions: `docker compose run --rm web python manage.py seed_reading_survey_questions`.
5. Create or register a parent/child and grant COPPA consent.
6. Get a JWT token from `POST /api/v1/auth/token/`.
7. Start a survey with `POST /api/v1/assessments/start-survey/`.
8. Fetch sections with `GET /api/v1/assessments/<id>/questions/?section=<kpi>`.
9. Submit answers progressively with `POST /api/v1/assessments/<id>/answer/`.
10. Complete the survey with `POST /api/v1/assessments/<id>/complete/`.
11. Confirm the response includes `reading_age`, `category_breakdown`, `strengths`, `growth_areas`, `teacher_summary`, and `final_message`.
12. Open `/admin/`, review the completed or human-review assessment, and add evaluator notes on the assessment result.

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
