# ClearCode CRM

## Purpose

The CRM is the central follow-up workspace for valid public form submissions. It uses the existing `Lead` record as the contact and keeps every accepted submission as an append-only `FormSubmission`, so a repeat inquiry updates the contact without destroying earlier intake evidence.

Current intake sources:

- consultation requests from `/contact/`;
- assessment follow-up requests from `/assessment/`;
- newsletter signups from public marketing pages; and
- generic website inquiries posted to `/crm/signup/`.

Invalid submissions and newsletter honeypot traffic are rejected before CRM records are created. Stored submission data uses an allowlist and excludes CSRF tokens and honeypot values.

Career-interest submissions are intentionally stored as `core.RecruitingInterest` records and managed in Django admin. They do not create CRM contacts, companies, or deals.

## Product model

The workspace follows familiar CRM patterns:

- a searchable contact index with status, audience, owner, sort, and view filters;
- summary counts for new/unassigned contacts, recent submissions, and overdue tasks;
- a contact record with identity and property panels;
- a unified activity timeline for form submissions, notes, and tasks; and
- quick owner/status updates plus task completion.
- company records shared by multiple contacts and deals;
- five deal pipelines with pipeline-specific stages; and
- a human triage queue for ambiguous family partner-interest signals.

These behaviors were informed by HubSpot's records index, record activity timeline, form-submission detail, and task-management documentation. ClearCode branding, authorization, and data handling remain independent.

## Data and authorization

- `Lead`: current contact properties and sales status.
- `Company`: an organization shared by its contacts and deals.
- `Opportunity` (shown as **Deal**): one relationship or funding process in exactly one of the five pipelines.
- `IntakeTriage`: the review record that preserves ambiguous intake signals before one or more deals are created.
- `FormSubmission`: immutable submitted field snapshot, form type, source path, and timestamp.
- `CrmActivity`: internal note or follow-up task with creator, owner, due date, and completion state.
- `NewsletterSubscription`: consent and unsubscribe state; this remains separate from CRM lead status.

The HTML workspace and leads API are restricted to superusers, staff, and central `SUPER_ADMIN` users. Form ingestion remains public but validates required fields and email syntax.

## Routes

- `/crm/` — contacts index.
- `/crm/contacts/<id>/` — contact record and activity timeline.
- `/crm/companies/` — company records with contact and deal rollups.
- `/crm/deals/` — pipeline-specific deal boards.
- `/crm/triage/` — pending intake routing decisions.
- `/crm/signup/` — public inquiry ingestion.
- `/newsletter/subscribe/` — public newsletter consent and CRM ingestion.

## Deal pipelines

1. Families / Enrollment
2. Referral Partners
3. Foundation Donors
4. Foundation Grants / PRIs
5. Equity / Investment

Each deal belongs to one pipeline. A company pursuing more than one capital structure gets separate linked deals—for example, one Foundation Grants / PRIs deal and one Equity / Investment deal—while the company and contacts remain single records. Recruiting is not a pipeline.

Family consultation and assessment follow-up intake creates or reuses one open Families / Enrollment deal. If the family survey's partner checkbox is selected, the raw response is retained and an `IntakeTriage` item is created. Staff choose the appropriate destination pipeline or pipelines; the system never creates several partner deals merely because the checkbox was selected.

## Verification

Run:

```shell
python manage.py test apps.crm
python manage.py check
python manage.py makemigrations --check --dry-run
```

Use a PostgreSQL-backed test database because the production project uses `django-tenants` with PostgreSQL.
