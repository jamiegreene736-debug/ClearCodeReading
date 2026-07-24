# Decision-support schema (Technical Spec §5.7)

The `decision_support` app fulfills Technical Spec §5.7's **design now, populate
later** requirement. It defines the V2 storage contracts for instructional
review flags, milestone estimates, milestones, and de-identified outcome
reporting without implementing the V2 evaluation, prediction, or aggregation
engines.

- `Flag` uses the deterministic low-growth codes in
  `docs/INSTRUCTIONAL_DESIGN.md` and stores the triggering rule, evidence
  snapshot, source session or curriculum position, routing, and rule version.
- `Prediction` stores one instructional target, one sessions-or-date estimate,
  confidence, evidence, model version, generation time, and a required planning
  disclaimer.
- `Milestone` stores a child-centered definition tied to one curriculum
  position or education skill band, with target/achievement dates and status.
- `OutcomeAggregate` is aggregate-only: its only relationship is to a center.
  It has no child, specialist, staff, audit-user, or free-form evidence
  relationship, and enforces a minimum cohort size of five.

All records are center-scoped. Child-level relationships validate that the
child, session, milestone, and curriculum position belong to the same center.
Read-only API querysets are membership-filtered; outcome endpoints additionally
require center leadership membership.

The functions in `apps/decision_support/services.py` intentionally return no
records. V2 work will populate their logic using the signals already captured
by sessions, placements, and mastery records.
