# ClearCode CRM

## Purpose

The CRM is the central follow-up workspace for valid public form submissions. It uses the existing `Lead` record as the contact and keeps every accepted submission as an append-only `FormSubmission`, so a repeat inquiry updates the contact without destroying earlier intake evidence.

Current intake sources:

- consultation requests from `/contact/`;
- assessment follow-up requests from `/assessment/`;
- career-interest submissions from `/careers/`;
- newsletter signups from public marketing pages; and
- generic website inquiries posted to `/crm/signup/`.

Invalid submissions and newsletter honeypot traffic are rejected before CRM records are created. Stored submission data uses an allowlist and excludes CSRF tokens and honeypot values.

## Product model

The workspace follows familiar CRM patterns:

- a searchable contact index with status, audience, owner, sort, and view filters;
- summary counts for new/unassigned contacts, recent submissions, and overdue tasks;
- a contact record with identity and property panels;
- a unified activity timeline for form submissions, notes, and tasks; and
- quick owner/status updates plus task completion.

These behaviors were informed by HubSpot's records index, record activity timeline, form-submission detail, and task-management documentation. ClearCode branding, authorization, and data handling remain independent.

## Data and authorization

- `Lead`: current contact properties and sales status.
- `FormSubmission`: immutable submitted field snapshot, form type, source path, and timestamp.
- `CrmActivity`: internal note or follow-up task with creator, owner, due date, and completion state.
- `NewsletterSubscription`: consent and unsubscribe state; this remains separate from CRM lead status.

The HTML workspace and leads API are restricted to superusers, staff, and central `SUPER_ADMIN` users. Form ingestion remains public but validates required fields and email syntax.

## Routes

- `/crm/` — contacts index.
- `/crm/contacts/<id>/` — contact record and activity timeline.
- `/crm/signup/` — public inquiry ingestion.
- `/newsletter/subscribe/` — public newsletter consent and CRM ingestion.

## Verification

Run:

```shell
python manage.py test apps.crm
python manage.py check
python manage.py makemigrations --check --dry-run
```

Use a PostgreSQL-backed test database because the production project uses `django-tenants` with PostgreSQL.
