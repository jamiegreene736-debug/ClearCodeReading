# Clear Code Reading

Clear Code Reading is a tenant-aware Django 5.1 platform for private reading-intervention
centers. It preserves the digital Reading Survey and existing school workflows while
adding a versioned instructional foundation for specialist-led intervention.

## Phase 0 instructional foundation

The authoritative methodology, placement, mastery, low-growth, and session-capture rules
are frozen in [`docs/INSTRUCTIONAL_DESIGN.md`](docs/INSTRUCTIONAL_DESIGN.md).

- Supported methodologies are Phonics for Reading (PFR) and IMSE Comprehensive
  Orton-Gillingham Plus (OG+).
- A child is actively placed in exactly one methodology; methodologies are not blended.
- `Curriculum` and `CurriculumSequence` provide center-scoped, versioned skill graphs.
- `StudentPlacement` records the current graph position and rationale;
  `StudentPlacementOverride` preserves specialist changes.
- `apps/sessions` contains model-only structured intervention capture and immutable
  revision snapshots. Session APIs and specialist UI are intentionally deferred.
- The legacy generic `Skill` model and digital Reading Survey remain intact.

After migrating a center schema, seed its initial graph positions:

```bash
docker compose run --rm web python manage.py seed_instructional_graphs --center-schema=center_schema
```

To seed all active centers:

```bash
docker compose run --rm web python manage.py seed_instructional_graphs --all-centers
```

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

Run tenant migrations after shared migrations:

```bash
docker compose run --rm web python manage.py migrate_schemas --tenant
```

Create an admin user:

```bash
docker compose run --rm web python manage.py createsuperuser
```

Useful URLs:

- Marketing homepage: `http://localhost:8000/`
- Family consultation: `http://localhost:8000/contact/`
- Public blog: `http://localhost:8000/blog/`
- Admin: `http://localhost:8000/admin/`
- Optional browser survey: `http://localhost:8000/assessment/`
- Swagger docs: `http://localhost:8000/api/docs/`
- ReDoc: `http://localhost:8000/api/redoc/`
- OpenAPI schema: `http://localhost:8000/api/schema/`
- Health check: `http://localhost:8000/api/v1/health/`
- CRM contacts, companies, deals, and triage: `http://localhost:8000/crm/` (central staff only)

Demo credentials:

- Admin: `admin@clearcodereading.com` / `ClearCodeDemo!2026`
- Teacher: `teacher@clearcodereading.com` / `ClearCodeDemo!2026`
- Parent: `parent@clearcodereading.com` / `ClearCodeDemo!2026`

Create or refresh demo credentials:

```bash
docker compose run --rm web python manage.py seed_demo_login
```

On Railway, the deploy start command runs migrations, seeds the Reading Survey question bank, and creates these demo credentials automatically.

### Publishing blog posts

Staff can create and manage articles at `/admin/blog/blogpost/` or use the **Blog posts**
shortcut in the administrator dashboard. New articles start as drafts. Set the status to
**Published** to publish immediately, or choose a future **Published at** time to schedule the
article. Draft and future-dated articles are never returned by the public blog views. Cover
images are optional; when one is supplied, its accessible image description is required.

The public archive also includes Bethany Fleming's **ClearCode Reading** Substack posts.
The server reads the publication's RSS feed, caches it for 15 minutes, and links each external
entry to its canonical Substack article. If Substack is slow or unavailable, `/blog/` continues
to serve locally published articles. Configure `BLOG_SUBSTACK_FEED_URL`,
`BLOG_SUBSTACK_CACHE_SECONDS`, and `BLOG_SUBSTACK_TIMEOUT_SECONDS` in the environment; set the
feed URL to an empty value to disable the integration.

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
- `/api/v1/schedule-bookings/recommendations/`
- `/api/v1/schedule-bookings/operations-metrics/`
- `/api/v1/schedule-bookings/<id>/approve/`
- `/api/v1/schedule-bookings/<id>/sync/`
- `/api/v1/schedule-bookings/reconcile-inbound/`

## Phase 2: family progress and scheduling

The parent dashboard is generated directly from completed intervention sessions, skill progress,
mastery records, and active placement. It exposes foundational-skill mastery, accuracy and WCPM
trends, decodable-text work, the latest specialist note, home practice, and a clearly labeled
sequence-position milestone estimate. Guardians must have an active relationship, all required
consents, and `permissions.progress_dashboard` must not be `false`.

Scheduling remains advisory until staff approve a proposal. Group recommendations require the
same methodology, adjacent sequence positions, overlapping student/provider availability, and
completed IEP authorization when applicable. Approved bookings can be pushed through the
configured `SCHEDULER_ADAPTER`; `reconcile-inbound` applies remote changes idempotently by external
booking ID. Configure an adapter implementing `upsert_booking()` and `pull_bookings()` for Jane App
or Acuity.

Operations metrics surface the launch thresholds: 75% utilization, 25 active waitlist entries,
and 40% demand from one submarket.

The persistent optimizer, proposal/approval payloads, Acuity configuration, consent safeguards,
and capacity definitions are documented in
[`docs/SCHEDULING_OPTIMIZER.md`](docs/SCHEDULING_OPTIMIZER.md).
- `/api/v1/mastery-records/`

CRM:

- `/crm/` — staff contacts, form-submission activity, notes, and tasks
- `/crm/companies/` — companies shared by contacts and deals
- `/crm/deals/` — five pipeline-specific deal boards
- `/crm/triage/` — human review for ambiguous intake routing
- `/api/v1/leads/`
- `/api/v1/leads/<id>/qualify/`
- `/api/v1/leads/<id>/convert/`
- `/api/v1/opportunities/`
- `/api/v1/companies/`
- `/api/v1/deals/`
- `/api/v1/opportunities/<id>/advance/`

See [`docs/CRM.md`](docs/CRM.md) for intake sources, data retention, authorization,
and verification details.

## Assessment Workflow

Clear Code Reading uses a human-in-the-loop assessment path:

1. An assessment starts as `pending`.
2. Digital submission moves it to `human_review`.
3. Evaluators review and complete it.
4. Completion updates progress records and queues parent notifications.

Celery tasks notify evaluators when human review is needed and send parent progress reports after review.

## Reading Survey

The Reading Survey is a child-friendly, education-side snapshot across reading skills. It can
create an `Assessment`, serve progressive questions, save answers as
`ChildAssessmentResponse` records, compute a reading-age estimate, and move the assessment into
`human_review`. It is supporting context only: methodology-specific evidence and specialist
review determine PFR or OG+ placement.

The browser experience at `/assessment/` is an optional, secondary marketing survey. The
primary public journey is the specialist-intervention consultation at `/contact/`. The
database-backed assessment workflow is available through the `/api/v1/assessments/` endpoints
below. After the child-facing questions, the browser survey runs the grade-routed Parent Reading
Inventory for Kindergarten, Grade 1, Grade 2, or Grade 3 and above. Each statement uses a Yes/No
answer, checkpoint scores can end the inventory early with a reading-support recommendation, and
completed inventories route families to either support or comprehension, fluency, and book-list
resources. ZIP Code, grade, and inventory answers remain in the browser session and are not added
to the consultation submission.

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

### Outcomes Data Asset

Cap 5 outcome reporting lives in `apps.outcomes`. It aggregates existing
sessions, placements, progress, and mastery records into immutable,
de-identified `DeIdentifiedOutcomeSnapshot` rows by center key, methodology,
grade band, and time window. The reporting API returns grouped snapshots only;
it deliberately excludes child PII, guardian data, specialist names, specialist
emails, and child-level rows.

Run the first aggregation with:

```bash
.venv/bin/python manage.py aggregate_outcomes --window-type quarter --aggregate-version v1
```

See `docs/outcomes_data_asset.md` for the stored metrics, excluded data, API
surface, and an example snapshot payload.

### Cap 2 decision support

`apps.decision_support` generates explainable, advisory low-growth flags and
milestone predictions from existing session and placement data. Session
completion runs the deterministic engine asynchronously; specialists can
review and acknowledge center-scoped flags through the API or Django admin,
and the parent dashboard uses the current prediction when one is available.

See `docs/decision_support.md` for rule behavior, on-demand commands, endpoints,
configuration, and example flag and prediction payloads.

- Parent/guardian registration creates a child profile and guardian relationship.
- Consent logs track consent type, status, version, source, IP, user agent, and expiry.
- Formal `ConsentRecord` history is the IDEA/IEP authorization source of truth;
  legacy child-profile approvals remain a fallback only when no record exists.
- Assessment, progress, mastery, and personalized lesson flows enforce active COPPA consent.
- Revoked or expired consent blocks sensitive child learning updates.
- Audit logs capture consent and assessment-status events for compliance review.

Production deployments should connect real email/SMS providers, keep `PUBLIC_APP_URL` accurate, store secrets outside git, and review data-retention rules with counsel.

### Careers intake

The Careers form sends introductions to the dedicated recruiting communication queue, separate from sales CRM contacts. It captures contact details, referral source, resume, and cover letter; accepts PDF, DOC, and DOCX files up to 10 MB each; and stores new uploaded document contents with the recruiting record in PostgreSQL. Authorized staff review responses and download documents through the protected **Admin → Core → Recruiting interests** workflow.

### Newsletter workflow

Every public marketing page includes an explicit-consent newsletter form. Subscribers can opt out through the signed link included in every newsletter. Staff can compose a draft under **Admin → CRM → Newsletter campaigns**, preview the final copy, and use **Review & send** to confirm delivery. Saving a campaign never sends it.

Campaigns snapshot the active subscriber list on first send, deliver one message per recipient, and retain per-recipient success or failure records. Retrying a partially failed campaign sends only pending or failed deliveries; it does not resend successful messages or include people who subscribed later. Configure a production email backend, `DEFAULT_FROM_EMAIL`, and the public HTTPS `PUBLIC_APP_URL` before sending. Production sends fail closed and the admin button stays disabled while a development-only email backend or local URL is configured. `NEWSLETTER_SEND_STALE_MINUTES` controls when an interrupted send may be resumed.

See `docs/consent_records.md` for the formal IDEA/IEP consent model, backfill,
center-scoped API, and enforcement behavior.

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

## Phase 1: Intervention Intelligence

Phase 1 adds center-scoped session capture and deterministic assessment-to-intervention mapping:

- Curriculum-specific placement evidence for PFR and IMSE OG+ instruments
- Reproducible PFR basal/ceiling and OG+ concept/error-pattern rules
- Ranked recommended sequences with specialist confirmation or labeled override
- Active placements and skill-based grouping suggestions
- Fast session defaults from the active placement, structured capture, and immutable revisions
- Guardian-provided availability plus IEP/IDEA approval gates
- Provider-neutral advisory AI and purchased-scheduler integration boundaries

The digital Reading Survey remains supporting context. It never selects a methodology or
sequence position without curriculum-specific placement evidence.

Seed each center after shared migrations:

```bash
python manage.py migrate_schemas --shared
python manage.py seed_instructional_graphs --center-schema=<schema>
```

Generate a recommendation from an existing evidence record:

```bash
python manage.py generate_placement_recommendation --center-schema=<schema> --evidence-id=42
```

### Placement recommendation API example

Create structured evidence:

```http
POST /api/v1/placement-evidence/
Authorization: Bearer <specialist-token>
Content-Type: application/json

{
  "child": 12,
  "curriculum": 3,
  "instrument": "pfr_placement",
  "source": "manual",
  "status": "completed",
  "assessment_version": "2026.1",
  "instructional_grade_band": "grade_2",
  "raw_results": {
    "starting_part": "PFR-A-01",
    "parts": [
      {
        "position_code": "PFR-A-01",
        "items": [
          {"item_id": "w1", "correct": true, "latency_seconds": 3},
          {"item_id": "w2", "correct": false, "latency_seconds": 4}
        ]
      }
    ]
  },
  "supporting_context": {
    "reading_survey_assessment_id": 88,
    "external_reports": [{"type": "TOWRE-2", "received_at": "2026-07-20"}]
  }
}
```

Then generate and confirm or override:

```http
POST /api/v1/placement-evidence/42/recommend/

POST /api/v1/placement-recommendations/17/confirm/
Content-Type: application/json

{
  "final_position": 301,
  "override_rationale": "Required only when the final position differs from the recommendation.",
  "evidence_considered": {"item_set_ids": ["review-17"]}
}
```

### Session logging API example

Fetch intelligent defaults with `GET /api/v1/sessions/defaults/?child=12`, then submit:

```http
POST /api/v1/sessions/
Authorization: Bearer <specialist-token>
Content-Type: application/json

{
  "child": 12,
  "status": "completed",
  "scheduled_start": "2026-07-24T14:00:00Z",
  "started_at": "2026-07-24T14:02:00Z",
  "ended_at": "2026-07-24T14:57:00Z",
  "activities_completed": [
    {
      "code": "word_reading",
      "status": "completed",
      "minutes": 12,
      "item_set_id": "PFR-A-08-1A-WR-01"
    }
  ],
  "item_sets": {
    "word_reading": {
      "item_set_id": "PFR-A-08-1A-WR-01",
      "correct": 9,
      "total": 10,
      "items": [
        {
          "item_id": "w1",
          "correct": true,
          "latency_seconds": 3,
          "mode": "decoding",
          "prompt_level": "independent"
        }
      ]
    }
  },
  "accuracy_numerator": 9,
  "accuracy_denominator": 10,
  "time_to_mastery_signals": {
    "cumulative_sessions_at_position": 2,
    "first_attempt_accuracy": 78,
    "latest_accuracy": 90,
    "prompts_per_10_items": 1,
    "independent_transfer": true,
    "reteach": false
  },
  "error_patterns": [
    {"code": "short_vowel_confusion", "target": "a_to_e", "count": 1, "opportunities": 10}
  ],
  "behavioral_observations": [
    {"code": "self_correction", "rating": "consistent", "activity_code": "word_reading"}
  ],
  "next_session_direction": "Complete PFR Session 1b with a distinct item set.",
  "home_practice_suggestion": "Read the five assigned words once."
}
```

The API defaults `center`, `specialist`, active position, targeted position, methodology
part, and calculated accuracy. `GET /api/v1/sessions/logging-metrics/` reports same-day
capture against the 95% operating target.

### Specialist demo

1. Open the specialist dashboard and select a pending placement recommendation.
2. Review the deterministic rationale and the ranked five-position sequence.
3. Confirm the suggested position, or select another position and record the evidence-based rationale.
4. Start a session from `GET /api/v1/sessions/defaults/?child=<id>`.
5. Enter activity/item-set results, observable participation, next direction, and home practice.
6. Save the completed session and verify its revision snapshot in the API or Django admin.
7. Open grouping suggestions at `GET /api/v1/placement-recommendations/grouping-suggestions/`.

## Project Layout

```text
clearcodereading/
apps/
  ai/
  api/
  assessments/
  core/
  crm/
  curriculum/
  sessions/
  scheduling/
  notifications/
  progress/
  schools/
  tenants/
  users/
scripts/
```

## Specialist Logging Checklist

- Open **Rapid log** from the specialist dashboard or `/portal/sessions/rapid-log/`.
- Confirm the reader, position, and PFR 1a / PFR 1b / OG+ session part.
- Enter correct and attempted responses; activities and distinct item-set IDs are prefilled.
- Add controlled error-pattern or observable-behavior chips only when they apply.
- Review the editable instructional next-step and home-practice drafts, then complete the session.
- Confirm the success state and use **Log another** or return to the reader’s position.

Minimal API example:

```json
{"mode":"quick_complete","child":42,"accuracy_numerator":9,"accuracy_denominator":10}
```
