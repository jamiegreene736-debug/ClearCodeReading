# Formal Consent Records

`users.ConsentRecord` is the formal enforcement point for IDEA/IEP-aligned
services. Records are center-scoped, append-only, and versioned per child and
consent type. A new row records each grant, denial, revocation, or pending
decision along with the actor, evidence reference, timestamps, and optional
expiration.

For a child with an active IEP, `ChildProfile.idea_services_authorized` reads the
latest non-deleted `idea_iep` record. Only an unexpired `granted` record is
effective. Session creation, schedule generation, proposal approval, booking
sync, and other existing authorization checks continue to call that property.

The migration backfills formal IDEA/IEP records from the legacy
`ChildProfile` approval fields when a center and a meaningful legacy decision
exist. If no formal record exists, the property falls back to those legacy
fields so existing data remains usable during rollout.

Center owners and administrators can add records in Django admin or through:

```text
GET  /api/v1/consent-records/
POST /api/v1/consent-records/
GET  /api/v1/consent-records/<id>/
```

The API is center-scoped, does not permit in-place updates or deletes, and
writes an `AuditLog` entry for every new version.
