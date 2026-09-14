# Parent Reading Inventory workflow

## Scope and plan

1. Reuse the existing website's grade-specific Parent Reading Inventory questions and examples in a versioned server-side definition.
2. Add a child-specific invitation and durable mail history to each CRM contact; add the Assessments overview and safe routing test screen.
3. Validate section progression and stopping rules on the server; save drafts and reject stale submissions.
4. Record completion, create a review task, send the outcome-specific acknowledgment, and notify the contact owner.
5. Offer operator-entered consultation slots, prevent overlapping availability and duplicate bookings, and send calendar attachments to both participants.
6. Verify with PostgreSQL tests, Django checks, migration drift, responsive browser checks, and exact deployed commit read-back.

## Content and routing

`apps/crm/data/parent_inventory_v1.json` copies the existing `marketing-website/assessment.html` inventory, which matches the downloaded Parent Reading Inventory form. It contains Kindergarten (20), Grade 1 (25), Grade 2 (25), and Grade 3+ (24) questions, with examples counted as part of the question. Grade-specific stopping thresholds are enforced before accepting later-section answers.

The source has conflicting percentage/Yes-count wording. Exact full-inventory boundary scores K=12, Grade 1=15, Grade 2=18, and Grade 3+=18 route to staff review; earlier stopping rules take precedence. No diagnostic or placement decisions are made. Existing standalone website assessment behavior is unchanged.

Outcome emails use conservative default copy. Resources link to the existing `/resources/` page. Support links to the invitation's booking page. Ambiguous totals receive a review acknowledgment. Contact owners receive a private CRM result link, not answers in email. Unassigned contacts still receive a visible review task.

## Operations

Open **CRM → Contacts → contact → Send assessment**. Select an existing child or create a separate child record, review the recipient/message, and send. New child records do not create student login credentials or enroll a child. Use an existing child record to send a new inventory attempt without overwriting earlier responses.

**CRM → Assessments** supports pending/completed/review/booked/email-attention filters. Results show each answer, the scoring version and rule, send history, review acknowledgment, and reminder/retry actions. Preview/test evaluates routing without creating records or sending email.

Invitations expire after 30 days and can be withdrawn. Public pages are private-link-only, noindex, no-store, and no-referrer. Answers are stored server-side. Save and return retains partial or complete sections without submitting; Continue commits the section and submits on the last applicable section. Stale-tab submissions cannot overwrite newer saves.

Production email requires a real Django email backend and HTTPS `PUBLIC_APP_URL`. Mail is persisted before delivery and claimed transactionally. Rejected messages display Failed; interrupted SMTP delivery displays Uncertain. Staff must check provider records and wait five minutes before retrying uncertain sends. This intentionally avoids blindly retrying messages SMTP may already have accepted. Pending mail can be retried from the assessment record. Reminders are operator-initiated and limited to once per 24 hours.

Add Bethany's approved times under **Consultation availability**, selecting her CRM account as host. Slots use explicit IANA time zones, reject DST ambiguity and overlapping slots, and are uniquely bookable. Booked appointments cannot be withdrawn through availability; coordinate changes with the parent. This availability is managed in ClearCode and does not sync an external calendar's busy times. Email attachments use stable calendar UIDs. No actual availability or parent outreach is seeded by deployment.

## Verification

Run `python manage.py test apps.crm.test_inventory apps.crm.tests` against an isolated PostgreSQL test database, then the full project suite, `manage.py check`, `makemigrations --check --dry-run`, and `git diff --check`.
