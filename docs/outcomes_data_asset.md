# Outcomes Data Asset

Clear Code Reading stores Cap 5 outcome evidence in immutable, de-identified
`DeIdentifiedOutcomeSnapshot` rows. The table is generated from existing
operational records only: completed instructional sessions, canonical skill
observations, student placements, curriculum positions, and mastery records.
Specialists do not collect any additional data for this layer.

## Stored

- An anonymized `center_key` plus the internal center foreign key for access
  control.
- Methodology, grade band, metric scope, aggregate version, and time window.
- The privacy floor applied to the snapshot. The default is five students, and
  groups below the floor are not written.
- Aggregated metrics such as cohort size, completed sessions, mastery events,
  skill mastery rate, weighted accuracy rate, mean sessions to mastery, median
  sessions to mastery, sessions to position, and retention counts.
- Source row counts for auditability.

## Deliberately Excluded

- Child names, child IDs in API payloads, student identifiers, birth dates,
  guardian data, notes, accommodations, and learning-profile details.
- Specialist names, emails, IDs in API payloads, and narrative session notes.
- Any child-level row or event stream. Outcomes responses only return grouped
  snapshots.

## Example Snapshot Payload

```json
{
  "center_key": "f7ab78d4d6c6d2a1",
  "methodology": "pfr",
  "grade_band": "grade_1_2",
  "window_type": "quarter",
  "window_start": "2026-01-01",
  "window_end": "2026-03-31",
  "metric_scope": "core_outcomes",
  "aggregate_version": "v1",
  "privacy_floor": 5,
  "metrics": {
    "cohort_students": 24,
    "completed_sessions": 186,
    "mastery_events": 41,
    "students_with_mastery": 18,
    "skill_mastery_rate": 75.0,
    "average_accuracy_rate": 86.4,
    "weighted_accuracy_rate": 84.91,
    "mean_sessions_to_mastery": 6.33,
    "median_sessions_to_mastery": 6.0,
    "mean_sessions_to_position": 3.21,
    "median_sessions_to_position": 3.0,
    "retention": {
      "active_student_count": 24,
      "students_with_completed_sessions": 24
    }
  },
  "source_counts": {
    "cohort_students": 24,
    "completed_sessions": 186,
    "mastery_records": 41,
    "curriculum_positions": 12,
    "skill_observations": 214,
    "structured_session_signals": 83
  }
}
```

## First Aggregation Command

Run the previous completed quarter:

```bash
.venv/bin/python manage.py aggregate_outcomes --window-type quarter --aggregate-version v1
```

Run an explicit custom window:

```bash
.venv/bin/python manage.py aggregate_outcomes --window-type custom --start 2026-01-01 --end 2026-03-31 --aggregate-version v1
```

Limit the run to one center by ID or slug:

```bash
.venv/bin/python manage.py aggregate_outcomes --days 90 --center launch-center --aggregate-version v1
```

Re-running the same version for the same window is idempotent and returns the
existing immutable snapshots. To recompute after late data, use a new version
such as `--aggregate-version v2`.

Schedule the command nightly with cron or call
`apps.outcomes.tasks.aggregate_previous_quarter_outcomes` from the existing
Celery beat configuration. Production runs should leave
`OUTCOMES_MIN_COHORT_SIZE=5` or raise it; lowering it below five requires an
explicit privacy review.

## Reporting API

- `GET /api/v1/outcomes/snapshots/`
- `GET /api/v1/outcomes/snapshots/trends/`

The API is restricted to super admins and school-admin users with owner/admin
center membership. Every report request writes an `AuditLog` entry. Payloads are
safe for later Looker Studio or investor/Foundation reporting because they expose
only grouped, de-identified metrics.
