# ClearCode CRM

## Purpose

The CRM is the central follow-up workspace for valid public form submissions. It uses the existing `Lead` record as the contact and keeps every accepted submission as an append-only `FormSubmission`, so a repeat inquiry updates the contact without destroying earlier intake evidence.

Current intake sources:

- consultation requests from `/contact/`;
- assessment follow-up requests from `/assessment/`;
- early interest survey responses from `/survey/` and locally hosted blog articles;
- newsletter signups from public marketing pages; and
- generic website inquiries posted to `/crm/signup/`.

Invalid submissions and newsletter honeypot traffic are rejected before CRM records are created. Stored submission data uses an allowlist and excludes CSRF tokens and honeypot values.

Career-interest submissions are intentionally stored as `core.RecruitingInterest` records and managed in Django admin. They enter the ClearCode recruiting candidate pool with a named owner (configured with `RECRUITING_OWNER_EMAIL`, with an active staff fallback) and do not create CRM contacts, companies, or deals.

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
- `/crm/deals/new/` — create a convention-named deal.
- `/crm/deals/<id>/` — edit pipeline-specific deal properties.
- `/crm/triage/` — pending intake routing decisions.
- `/crm/signup/` — public inquiry ingestion.
- `/crm/survey/` — validated early interest survey ingestion from the main survey page and local articles.
- `/newsletter/subscribe/` — public newsletter consent and CRM ingestion.

## Deal pipelines

1. **Families / Enrollment** — Lead / Nurture, Waitlist, Consultation Scheduled, Assessment, Enrolled, Active, Lost, Churned. One deal per student, named `Student — term/year`, with funding type, ESA program, grade band, in-catchment ZIP, and referral source properties.
2. **School & Teacher Referral Partners** — Identified, Contacted, Meeting / Lunch-and-Learn, Active Referrer, Dormant. One partnership per organization, with partner type and priority properties.
3. **Foundation Donors** — Identified, Cultivation, Ask, Committed / Gift, Stewardship, Declined. Gift deals are named `Donor — campaign/year`, with donor type, priority, and gift level.
4. **Foundation Grants / PRIs** — Need Intro, Relationship Building, LOI / Application Invited, Application Submitted, Awarded, Declined. Applications are named `Funder — program — cycle year`; capital lane is Foundation, with grant-cycle date and bucket properties.
5. **Equity / Investment** — Need Intro, Introduced, First Meeting, Diligence / Data Room, Term Sheet, Closed-Won, Passed. Investments are named `Firm — round`, with ClearCode, Inc./Both capital lane, priority, and bucket properties.

Each deal belongs to one pipeline and one stage. Priority and comma-separated segment tags are properties, never substitute pipelines or stages. A company pursuing more than one capital structure gets separate linked deals—for example, one Foundation Grants / PRIs deal and one Equity / Investment deal—while the company and contacts remain single records. Parentheses are rejected in convention-driving name fields; separate work gets a second deal. Recruiting is not a pipeline.

Family consultation and assessment follow-up intake creates or reuses one open Families / Enrollment placeholder and marks it for naming review until the student and term/year are supplied. The assessment follow-up lets a family select Referral Partner, Donor, Advocate, or any combination of the three. Selections are retained separately on the submission and contact, exposed as a CRM contact filter, and sent together to one `IntakeTriage` item. Staff choose the appropriate referral, donor, and/or advocate path; Advocate is explicitly recorded without inventing a sixth deal pipeline. Resulting deal placeholders remain visibly marked for naming review, and the system never creates several deal records merely because several interests were selected.

The Early Interest Survey uses one server contract in both placements. Every accepted response stores normalized answers as an immutable `FormSubmission`, deduplicates the CRM contact by normalized email, records page/article attribution, and updates the latest survey properties on the contact. Parent-branch responses update the open Families / Enrollment deal with ZIP, grade band, funding signal, and waitlist intent. Ambiguous advocate/referral/donor selections enter human intake triage. Explicit survey email consent also creates or reactivates the separate newsletter subscription, preserving its source path and unsubscribe controls. Conditional Q5-Q9 answers and family-only engagement choices are enforced on the server, not only in browser JavaScript.

The `/assessment/` contact handoff stores a server-recomputed digital reading result plus the validated child, ZIP, grade, and parent-inventory answers. Those structured fields are visible in the CRM submission timeline and update the open family deal without trusting client-calculated score fields.

## Verification

Run:

```shell
python manage.py test apps.crm
python manage.py check
python manage.py makemigrations --check --dry-run
```

Use a PostgreSQL-backed test database because the production project uses `django-tenants` with PostgreSQL.
