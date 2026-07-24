# Scheduling Optimizer

The ClearCode optimizer is an advisory layer over a bought calendar. It never confirms
or externally publishes a group without explicit center-operations approval.

## Proposal lifecycle

`POST /api/v1/schedule-proposals/generate/` accepts a center, inclusive date range,
optional specialist, maximum sequence gap, and session duration. The optimizer:

1. Reads active, center-matched `StudentPlacement` records.
2. Excludes every child whose `idea_services_authorized` value is false.
3. Keeps PFR and OG+ placements separate, then ranks children within an adjacent
   `sequence_order` range.
4. Expands recurring child and provider availability in each window's IANA timezone.
5. Respects provider `max_group_size` and existing approved/confirmed conflicts.
6. Persists an advisory `ScheduleGroupProposal` plus one proposed
   `ScheduleBooking` per child.

Example request:

```json
{
  "center": 12,
  "start_date": "2026-08-03",
  "end_date": "2026-08-14",
  "specialist": 44,
  "max_position_gap": 1,
  "session_minutes": 60,
  "limit": 50
}
```

Example proposal excerpt:

```json
{
  "id": 901,
  "center": 12,
  "specialist": 44,
  "methodology": "pfr",
  "starts_at": "2026-08-03T15:00:00-04:00",
  "ends_at": "2026-08-03T16:00:00-04:00",
  "score": 90,
  "status": "proposed",
  "approval_required": true,
  "students": [
    {
      "child": 101,
      "display_name": "Avery Reader",
      "idea_services_authorized": true,
      "iep_consent_indicator": "authorized"
    }
  ]
}
```

Center owner/admin approval uses
`POST /api/v1/schedule-proposals/901/approve/`. Approval locks and revalidates the
entire group, including current placement, methodology, sequence proximity, and IDEA
authorization. It changes every child booking to `approved` in one transaction.
`POST /api/v1/schedule-proposals/901/reject/` cancels all proposed child bookings.

## External calendar boundary

`SchedulerAdapter` is vendor-neutral. `StubSchedulerAdapter` is deterministic and does
not use a network. `AcuitySchedulerAdapter` creates or reschedules admin appointments
and pulls scheduled/canceled appointments. Configure:

```text
SCHEDULER_ADAPTER=apps.scheduling.integrations.AcuitySchedulerAdapter
ACUITY_USER_ID=...
ACUITY_API_KEY=...
ACUITY_APPOINTMENT_TYPE_ID=...
ACUITY_CALENDAR_IDS={"44": 123456}
```

Jane App can be added behind the same `SchedulerAdapter` contract without changing
booking or optimizer code. Production scheduler credentials are configured through
environment variables or the deployment secrets store; never commit them. No live
Jane App or Acuity credential is required for local development or automated tests.

Use `POST /api/v1/schedule-bookings/<id>/force-sync/` after approval.
Failures persist as `sync_status=error`, `sync_error`, `sync_attempts`, and
`last_sync_at` so operations can retry. Inbound reconciliation only updates a local
booking with the same center, configured provider, and external ID; it never accepts a
remote child or center identity.

## Operations signals

`GET /api/v1/schedule-bookings/operations-metrics/?center=12` returns unique
confirmed/completed delivery hours against exact recurring provider-capacity hours,
active waitlist count, and every submarket concentration. PRD expansion thresholds are:

- sustained utilization of at least 75% over a range of at least 28 days;
- at least 25 active waitlist entries;
- at least 40% of waitlist demand from one submarket.

`signals.expansion_review_recommended` becomes true when all three conditions hold.
This is an operations signal, not an automatic expansion decision.
