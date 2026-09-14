# Parent Reading Inventory workflow

## Scope and plan

1. Reuse the existing website's grade-specific Parent Reading Inventory questions and examples in a versioned server-side definition.
2. Add a child-specific invitation and durable mail history to each CRM contact; add the Assessments overview and safe routing test screen.
3. Validate section progression and stopping rules on the server; save drafts and reject stale submissions.
4. Record completion, create a review task, send the outcome-specific acknowledgment, and notify the contact owner.
5. Offer operator-entered consultation slots, prevent overlapping availability and duplicate bookings, and send calendar attachments to both participants.
6. Verify with PostgreSQL tests, Django checks, migration drift, responsive browser checks, and exact deployed commit read-back.

## Content and routing

`apps/crm/data/parent_inventory_v1.json` copies the existing `marketing-website/assessment.html` inventory, which matches the downloaded Parent Reading Inventory form. It contains Kindergarten (20), Grade 1 (25), Grade 2 (25), and Grade 3+ (24) questions, with examples counted as part of the question. All grade-specific questions must now be answered before submission. Former stopping thresholds still select the support outcome, but no longer prevent answering later questions.

The source has conflicting percentage/Yes-count wording. Exact full-inventory boundary scores K=12, Grade 1=15, Grade 2=18, and Grade 3+=18 route to staff review; section support thresholds take precedence after all questions are answered. No diagnostic or placement decisions are made. Existing standalone website assessment behavior is unchanged.

Outcome emails use conservative default copy. Resources link to the existing `/resources/` page. Support links to the invitation's booking page. Ambiguous totals receive a review acknowledgment. Contact owners receive a private CRM result link, not answers in email. Unassigned contacts still receive a visible review task.

## Operations

Open **CRM → Contacts → contact → Send assessment**. Select an existing child or create a separate child record, review the recipient/message, and send. New child records do not create student login credentials or enroll a child. Use an existing child record to send a new inventory attempt without overwriting earlier responses.

**CRM → Assessments** supports pending/completed/review/booked/email-attention filters. Results show each answer, the scoring version and rule, send history, review acknowledgment, and reminder/retry actions. Preview/test evaluates routing without creating records or sending email.

Invitations expire after 30 days and can be withdrawn. Public pages are private-link-only, noindex, no-store, and no-referrer. Answers are stored server-side. Save and return retains partial or complete sections without submitting; Continue commits the section and submits on the last applicable section. Stale-tab submissions cannot overwrite newer saves.

Production email uses the connected Google mailbox of the CRM user who created the invitation. Invitations, follow-ups, owner notifications, and calendar messages enter the existing CRM email worker and appear in its outbox. Google email configuration, the sender's connected mailbox, an operational email worker, and HTTPS `PUBLIC_APP_URL` are required. Missing setup is a visible failure; queued is not sent. The existing worker owns backoff and ambiguous-send reconciliation. The inventory mirrors its delivery receipts. Only the original sender may retry or send reminders from their mailbox. Revoked or expired invitation links are rejected before queued delivery. Development with Google email disabled can use Django's local test backend.

SMTP development tests also cover failed/uncertain deliveries. Uncertain Google sends remain in the worker's reconciliation flow and are not blindly resent. Reminders are operator-initiated and limited to once per 24 hours.

Add Bethany's approved times under **Consultation availability**, selecting her CRM account as host. Slots use explicit IANA time zones, reject DST ambiguity and overlapping slots, and are uniquely bookable. Booked appointments cannot be withdrawn through availability; coordinate changes with the parent. This availability is managed in ClearCode and does not sync an external calendar's busy times. Email attachments use stable calendar UIDs. No actual availability or parent outreach is seeded by deployment.

## Verification

Run `python manage.py test apps.crm.test_inventory apps.crm.tests` against an isolated PostgreSQL test database, then the full project suite, `manage.py check`, `makemigrations --check --dry-run`, and `git diff --check`.

### Release validation (2026-09-14)

- Full combined project suite: 395 tests passed after integrating CRM Google email and hiring changes.
- Focused assessment/email suite passed before the full run; tests cover grade counts, early exits, ambiguous totals, saved full/partial sections, stale tabs, sibling isolation, CSRF, access control, idempotent sends/submissions/bookings, slot conflicts, Google queue receipts, calendar MIME, and email failures.
- Django system checks, migration drift checks, Ruff checks/formatting for changed workflow modules, and whitespace checks passed.
- Parent survey and CRM compose layout were inspected at desktop and 390px mobile widths with no horizontal overflow.
- Production Google email is currently disabled pending provider setup. No live parent emails or consultation availability were seeded. `PUBLIC_APP_URL` is set to the verified Railway HTTPS site on web and email worker services.
- Final Google email integration follow-up: 73 focused tests passed, plus a dedicated worker-recovery regression. Strict email mypy checks passed for 17 source files. The worker now picks up pending inventory outbox rows left by interrupted web requests.

### Send confirmation

The send form shows a focused error summary when validation prevents sending and preserves entered values. Submission disables the button and announces progress. The assessment record displays the latest invitation/reminder receipt prominently: queued, sending confirmation pending, sent, or not sent. Sent means provider acceptance, not recipient opening. Failures retain the invitation and link to email settings and the existing retry controls.

While delivery is pending, a CRM-authorized, non-cacheable status endpoint refreshes the confirmation and email history every ten seconds while the page is visible, up to ten minutes of active polling. Manual refresh remains available. Receipt refreshes preserve expanded messages and defer history replacement while a user is interacting with it. Duplicate submissions preserve the original send timestamp.

Verification: 37 focused inventory tests; Django system/migration checks; Ruff on changed Python modules; existing strict email type checks. Desktop and 390px mobile checks confirmed visible validation, retained input, responsive failure/success cards, and queued-to-sent automatic updates without horizontal overflow or browser errors. UI checks used synthetic local receipts and did not send external email.

### Invitation email layout

The assessment action appears before a standalone “Thank you” sign-off, after the message's save-and-return paragraph. Edited messages without that sign-off retain their full text before the action. The CRM preview, HTML email, and plain-text email use the same ordering. HTML email and the CRM preview include the existing ClearCode logo; outgoing email uses an absolute public asset URL with alternative text. Desktop and 390px HTML previews verified the logo and button position; 39 focused tests passed, including Google outbox ordering, escaped content, and custom messages.


## September 14: full completion and per-user appointments

The previous implementation marked a low-scoring section as a completed inventory.
Live investigation found a kindergarten invitation with 11 of 20 answers and rule
`section-1-below-9`. All 20/25/25/24 grade-specific questions are now required.
Existing valid links with early completions offer **Answer remaining questions**.
This explicit action preserves answers and the earlier result in the audit log,
reuses the review task, and issues updated follow-up receipts on completion.
Fully completed, expired, and withdrawn invitations cannot be reopened this way.

### Appointment plan and first version

1. **Use the existing consultation slots and booking records.** These already
   provide one booking per slot and invitation, time zones, overlap checks,
   durable email receipts, and calendar attachments. Do not duplicate them in the
   instructional group-scheduling subsystem.
2. **First version implemented here:** show Bethany Fleming by default in CRM and
   public booking, with a host selector and a My availability shortcut. Ordinary
   CRM users add/confirm/withdraw only their own times. Super administrators may
   propose another user's time, but only that host can confirm it; proposed times
   are not bookable. Confirmations lock the host and recheck overlaps. Booked slots
   cannot be withdrawn. Existing active times retain their previous approval.
   `CRM_DEFAULT_CONSULTATION_HOST_EMAIL` can pin the default to an exact account;
   without it only a unique active CRM account named Bethany Fleming is selected.
   An absent/ambiguous default shows the available team times instead of guessing.
3. **Next phase: recurring hours and exceptions.** Add a host scheduling profile
   with time zone, weekly hours, meeting length, buffers, and a booking horizon.
   Generate dated slots idempotently; let users confirm a week at a time and block
   vacations or single dates. Keep unconfirmed dates unavailable.
4. **Next phase: connected-calendar busy checks.** Reuse each user's connected
   Google account, request the minimum calendar permission, and check busy times
   when displaying and again when booking. Show a clear unavailable state when
   freshness cannot be verified. Keep local bookings authoritative for duplicate
   protection and record provider synchronization failures visibly.
5. **Next phase: rescheduling, cancellation, reminders.** Use explicit booking
   states and audited transitions; update stable calendar UIDs and notify both
   parties through the existing durable outbox. Add retries/idempotency and an
   operator queue for failures. Do not remove booked slots as a cancellation shortcut.

Acceptance checks include all-No answers through every grade, legacy continuation,
partial save, stale tabs, browser radio interaction, CSRF, host authorization,
unconfirmed-slot rejection, overlap rejection, Bethany default selection, and
existing booking/email idempotency. No real appointment times are invented.

Verification for this change: 503 project tests and 51 focused tests passed; Django system/migration checks, Ruff, and strict email type checks passed. Chromium and WebKit exercised all 25 Grade 2 answers, save/resume, and submission at 390px and 1280px. Chromium exercised Bethany default selection, confirmation, and withdrawal at both widths. No horizontal overflow or assessment browser errors.

## Google Calendar sign-in

Consultation availability now has a direct **Connect Google Calendar** button. It requests
`openid`, `email`, and `calendar.freebusy` for the user's primary Google calendar. Calendar
consent is independent of Gmail consent, stored against the signed-in CRM host, and protected
by expiring single-use state, session binding, nonce verification, and PKCE. Refresh tokens
use the calendar encryption key derivation. No event descriptions, attendees, or Gmail data
are requested by this flow. Existing Apple/iCal connections remain a secondary option.

Google busy-time reads run through the existing availability filtering and booking recheck.
Malformed responses, revoked grants, and provider errors hide the connected host's slots;
a successful read clears the error. A connection is saved only after its first free/busy read
succeeds. Disconnect deletes the locally stored token without revoking a shared Google OAuth
client's Gmail grant. Hosts can remove the app's Google permissions separately in Google Account.

Provider setup:
- Enable `calendar-json.googleapis.com` in the Google Cloud project owning the OAuth client.
- Allow `https://www.googleapis.com/auth/calendar.freebusy` in the consent configuration and
  any applicable Workspace administrator controls.
- By default, calendar sign-in reuses `CRM_EMAIL_GOOGLE_CLIENT_ID`,
  `CRM_EMAIL_GOOGLE_CLIENT_SECRET`, and the registered `CRM_EMAIL_REDIRECT_URI`.
  The existing `/crm/email/callback/` dispatches calendar-prefixed state to the isolated calendar
  handler before Gmail processing. No Gmail permission is needed for calendar connection.
- Optional `CRM_CALENDAR_GOOGLE_CLIENT_ID`, `CRM_CALENDAR_GOOGLE_CLIENT_SECRET`, and
  `CRM_CALENDAR_REDIRECT_URI` allow a separate OAuth client. Register the same callback route
  on the canonical CRM hostname. An Internal client permits Workspace accounts only; personal
  Gmail accounts require an External client/audience with applicable Google verification and
  test-user restrictions resolved. Do not broaden the Gmail client's audience implicitly.
- Every host completes Google's consent themselves. Confirm a live connected account and
  a busy-time collision before claiming end-to-end provider verification.

References: https://developers.google.com/identity/protocols/oauth2/web-server and
https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query.
