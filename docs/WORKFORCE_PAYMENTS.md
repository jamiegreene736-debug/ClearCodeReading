# ClearCode workforce and contractor payments

## Decision and scope

ClearCode is the payer of record. The first operating state is Florida. This module keeps
worker classification separate from a user's instructional role and from center access:
being a `teacher` or `specialist` in the application never makes someone an independent
contractor.

Phase 1 deliberately does not store tax identifiers, bank-account details, identity
documents, or completed tax forms. A provider-neutral adapter invites the worker to a
third-party system, and ClearCode stores only opaque provider identifiers, normalized
status, deadlines, remediation codes, and audit evidence. Production fails closed until a
real provider adapter is selected and configured.

The future internal-vault phase may change the custodian on a sensitive-data reference to
`internal_vault`, but only after a separate security review. Restricted values must live in
an isolated encryption service with field-level envelope encryption and separately managed
keys; they must never be added to the workforce database, logs, analytics, backups, or
ordinary administrator pages.

## Roles and separation of duties

- `workforce_admin`: manages worker records, assignments, agreements, onboarding, and rates.
- `compliance_reviewer`: records classification decisions and compliance outcomes.
- `finance_preparer`: creates payment runs; a different preparer reviews them.
- `finance_approver`: gives final payment-run approval.
- Center owner/admin: approves completed work only for that center.
- Worker: sees their own engagement, onboarding status, compliance status, and payables.

The person who creates a rate cannot approve it. The person who submits a payable cannot
approve it. A payment-run creator cannot be its reviewer or final approver, and the reviewer
cannot be the final approver.

## Workflow

1. Create a worker profile and a `pending` engagement.
2. Assign the engagement to one or more centers.
3. A compliance reviewer records a versioned classification decision and next-review date.
4. If the decision is contractor, create the Florida new-hire task and a provider invite.
5. The worker follows the one-time provider URL returned by the invite action. The URL and
   its token are never persisted by ClearCode.
6. Provider status, signed agreement, required screening, and Florida reporting determine
   payment readiness.
7. A completed instructional session can create one draft payable using the effective,
   approved rate. A center owner/admin approves that work.
8. Finance groups approved payables into an idempotent payment run, then separate staff
   review and approve the run before provider submission.
9. Provider events are deduplicated by external event ID and stored as hashes and normalized
   outcomes only. Raw webhook bodies are not retained.

## Florida and federal rules represented by the system

The module maintains separate tasks and thresholds because Florida new-hire reporting and
federal Form 1099 filing are different obligations.

- Florida independent-contractor reporting applies when a service recipient pays an
  individual $600 or more in a calendar year. The report is due within 20 days after the
  earlier triggering event defined by Florida law. The exact trigger date and due date are
  stored so staff can review them.
- Worker classification remains a human legal/compliance decision. The application records
  evidence and approval; it does not infer contractor status from a teaching role.
- The federal information-return threshold is stored on each tax-year summary rather than
  hard-coded forever. The configured default is $2,000 for 2026 and must be reviewed for
  each later year.
- Background screening and E-Verify tasks are conditional on delivery context and employee
  status. They are not silently applied to every contractor.

Operational owners should confirm final procedures with ClearCode's accountant and Florida
employment counsel before the first live payment.

## Provider adapter contract

An adapter implements invite creation, onboarding status retrieval, payment submission,
payment status retrieval, and signed webhook normalization. Adapters return normalized data
and must not return tax IDs or bank data. Calls that can create money movement require an
idempotency key. The deterministic stub is limited to development and tests.

Environment settings:

- `WORKFORCE_PROVIDER_ADAPTER`: dotted adapter class path. Leave empty in production until
  the provider is selected.
- `WORKFORCE_ALLOW_STUB_PROVIDER`: may be `1` only in local development/test environments.
- `WORKFORCE_FLORIDA_REPORTING_THRESHOLD`: defaults to `600.00`.
- `WORKFORCE_FEDERAL_1099_THRESHOLD_2026`: defaults to `2000.00`.

## Security invariants

- No SSN, TIN, EIN, routing number, account number, date of birth, identity-document image,
  or signed W-9 field exists in this schema or API.
- Opaque external IDs are never accepted from ordinary worker-facing update endpoints.
- Audit payloads use allowlisted operational fields and never copy full request/provider
  payloads.
- Querysets are scoped to central role, center assignment, or the authenticated worker.
- State transitions run in database transactions and lock records before approving or
  submitting them.
- Money amounts are decimal values; status transitions and idempotency keys are explicit.

## Source material

- [Florida Statutes 409.2576](https://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&URL=0400-0499/0409/Sections/0409.2576.html)
- [Florida Department of Revenue worker classification](https://www.floridarevenue.com/taxes/taxesfees/Pages/rt_employee.aspx)
- [Florida Statutes 501.171](https://www.leg.state.fl.us/Statutes/index.cfm?App_mode=Display_Statute&URL=0500-0599%2F0501%2FSections%2F0501.171.html)
- [IRS electronic Form W-9 requirements](https://www.irs.gov/instructions/iw9)
- [IRS information-return filing threshold](https://www.irs.gov/businesses/small-businesses-self-employed/am-i-required-to-file-a-form-1099-or-other-information-return)
