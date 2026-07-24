# Cap 2 decision support

`apps.decision_support` provides advisory, explainable instructional review
flags and milestone estimates from data already stored in `Session` and
`StudentPlacement`. It never changes a placement, curriculum position,
instructional intensity, or methodology.

## Runtime flow

When a session first becomes `completed`, a transaction-safe signal queues
`apps.decision_support.tasks.evaluate_completed_session`. The task:

1. evaluates all six deterministic definitions in
   `docs/INSTRUCTIONAL_DESIGN.md`;
2. creates or refreshes one open `GrowthFlag` per child, position, and code;
3. stores the exact session IDs, accuracy values, error codes, thresholds, and
   engine version used in the decision;
4. routes every flag to the session specialist and high-severity flags to
   configured center leadership; and
5. writes a new current `MilestonePrediction` when the child has an active
   placement.

High-severity notifications are optional in practice: when routed users have
email addresses, the existing notification service sends an advisory review
message. `DECISION_SUPPORT_LEADERSHIP_ROLES` controls the center membership
roles included in this routing.

The engine is replaceable through `DECISION_SUPPORT_ENGINE`. A future in-house
model must implement the two methods in
`apps.decision_support.interfaces.DecisionSupportEngine`; API, task, and
dashboard consumers do not need to change.

## On-demand use

Center-scoped API endpoints:

- `GET /api/v1/growth-flags/?status=open`
- `POST /api/v1/growth-flags/{id}/acknowledge/`
- `POST /api/v1/growth-flags/{id}/resolve/`
- `POST /api/v1/growth-flags/evaluate-session/`
- `GET /api/v1/milestone-predictions/?child={id}&current=true`
- `POST /api/v1/milestone-predictions/generate/`

The Django admin provides an open-flag list and an acknowledge action. A
center-explicit management command is also available:

```bash
python manage.py run_decision_support --center-schema CENTER --session-id 123
python manage.py run_decision_support --center-schema CENTER --child-id 456
```

## Example growth flag

```json
{
  "flag_code": "flat_accuracy",
  "severity": "medium",
  "status": "open",
  "position_code": "PFR-A-03",
  "explanation": "This flag fired because accuracy changed from 70.0% to 74.0% (4.0 percentage points) across four completed captures at PFR-A-03, below the 5-point growth threshold.",
  "advisory_recommendation": "Review grouping, pacing, prompting, and item variation; any adjustment remains a specialist decision.",
  "evidence_snapshot": {
    "engine_version": "deterministic-2026.1",
    "position_code": "PFR-A-03",
    "first_accuracy": 70.0,
    "latest_accuracy": 74.0,
    "percentage_point_gain": 4.0,
    "threshold": 5,
    "sessions": [
      {"session_id": 101, "accuracy": 70.0, "reteach": false, "error_patterns": []},
      {"session_id": 105, "accuracy": 72.0, "reteach": false, "error_patterns": []},
      {"session_id": 109, "accuracy": 73.0, "reteach": false, "error_patterns": []},
      {"session_id": 113, "accuracy": 74.0, "reteach": false, "error_patterns": []}
    ]
  }
}
```

## Example milestone prediction

```json
{
  "target_label": "Current sequence completion",
  "current_position": "PFR-A-03",
  "target_position_code": "PFR-A-05",
  "predicted_sessions": 8,
  "predicted_date": "2026-09-18",
  "confidence": "medium",
  "confidence_band_sessions": {"lower": 6, "upper": 11},
  "parent_timeline": "At the recent pace, sequence completion is estimated around September 2026, with roughly 6 to 11 more sessions. This range can change as new progress is recorded.",
  "disclaimer": "This is an instructional planning estimate based on recent progress and attendance. It may change and is not a guarantee.",
  "evidence_summary": {
    "engine_version": "deterministic-2026.1",
    "sessions_per_position": 2.5,
    "sessions_per_position_source": "child_history",
    "child_completed_position_counts": [2, 3],
    "observed_weekly_session_rate": 1.0
  }
}
```

Predictions prefer the child's own completed-position history, then comparable
history within the same center-owned curriculum, then a clearly labeled
two-check planning default. Sparse defaults always use low confidence and a
wider range. All outputs remain estimates, never guarantees.
