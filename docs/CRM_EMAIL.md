# CRM Google email implementation

Approved scope: individual Workspace mailboxes; contact conversations; compose/reply/reply-all;
CC/BCC, safe formatting, signatures, private attachments; selected historical import;
team-visible linked history; owner-only drafts and sending; templates, scheduled sends and
CRM follow-up tasks. Bulk campaigns are not part of this integration.

## Implementation sequence
1. Add shared-schema data models, encrypted credentials/attachments and explicit permissions.
2. Add Google authorization, verified Workspace identity, revocation and authenticated Pub/Sub.
3. Add durable database outbox, safe send reconciliation, bounded synchronization and recovery.
4. Add CRM email settings and contact workspace, drafts, imports, templates and scheduling.
5. Verify with PostgreSQL tests, security cases and rendered desktop/mobile pages.
6. Merge reviewed PR, deploy web and worker, verify exact commit and readiness.
7. Configure organization-owned Google credentials and test two consenting pilot accounts.

## Access and privacy
Current CRM contacts are shared among active CRM-authorized users. Linked email history follows
that same access rule. Drafts, outbox, BCC and templates are private to their owner. No user can
send through another user's mailbox. Email endpoints operate only on the public schema.
Connection grants read access to the mailbox but imports only linked threads or explicitly
selected historical threads. Matching a contact email alone never automatically publishes a
thread. Google identity must match the signed-in CRM email and verified Workspace hosted domain.
Reconnecting must use the same Google subject. Aliases/Groups are not independent mailboxes.

## Operations
The database is the durable queue. A dedicated `python manage.py crm_email_worker` process
runs bounded passes, using a PostgreSQL advisory lock to prevent concurrent passes. No Redis
service is required. Worker heartbeat and mailbox synchronization times appear in settings.
Sending is marked in-flight before the network request. Timeouts, malformed send responses and
server errors become uncertain and are reconciled against Sent using a stable Message-ID;
they are never blindly resent. Definitive rate-limit responses use bounded backoff with jitter.
Refresh-token loss requires reconnecting. Repeated provider failures back off before trying again.
Daily watch renewal and periodic linked-thread recovery supplement authenticated Pub/Sub events.

Historical imports show a bounded 90-day candidate list from the connected user's mailbox and
require selection before publishing a thread on the contact. Imports are processed by the worker.
Attachments are encrypted in the database with a separate Fernet key, served only through
authorized download routes, never through public media storage. Total uploaded attachments are
limited to 10 MiB per message. Message bodies are sanitized; remote images and active HTML are
removed. Stored drafts/templates use the same sanitizer.

Retention: CRM-linked messages remain business records after disconnect. Disconnect removes
local credentials, cancels pending sends/imports, and requests Google revocation. An explicit
administrator purge command supports retention/deletion requests. No automatic age-based
purge is enabled. Backups must follow the organization's corresponding retention policy.

## Google organization setup
1. In an organization-owned Google Cloud project, enable Gmail API and Pub/Sub API.
2. Configure Google Auth Platform Audience as **Internal** and create a Web application OAuth
   client. Register the exact HTTPS callback `/crm/email/callback/` on the chosen CRM hostname.
   Request `openid`, `email`, `gmail.send`, and `gmail.readonly`. Grant access through Workspace
   admin app controls when required. Do not use domain-wide delegation or app passwords.
3. Create a Pub/Sub topic. Grant `roles/pubsub.publisher` on that topic to
   `gmail-api-push@system.gserviceaccount.com`. The topic project must match the OAuth project.
4. Create a service account for authenticated push. Configure a push subscription to
   `/crm/email/push/` with that account and the endpoint URL as the exact audience. Permit the
   Pub/Sub service agent to mint its OIDC token as documented by Google. The app validates the
   signature, issuer, audience, verified email and exact service account identity.
5. Set the variables in `.env.example` on BOTH web and email worker services. Generate a separate
   encryption key with `Fernet.generate_key()` and store it in deployment secrets. Never reuse
   Django's signing secret. Keep previous keys during rotation until retained data is re-encrypted.
6. Set `CRM_EMAIL_ENABLED=1` only after these settings are complete. Restart both services.
7. Each pilot user opens CRM > Email settings > Connect Google and consents personally. Their CRM
   email must match their primary Workspace email. Confirm two different accounts cannot access
   each other's drafts, templates or BCC, but can read explicitly shared CRM conversations.

Google consent cannot be completed on behalf of users without their participation. An external
OAuth audience, personal Gmail accounts, aliases and Google Groups require a separate review.

References: https://developers.google.com/workspace/gmail/api/auth/scopes,
https://developers.google.com/identity/protocols/oauth2/web-server,
https://developers.google.com/workspace/gmail/api/guides/push,
https://cloud.google.com/pubsub/docs/authenticate-push-subscriptions.

## Railway deployment and verification
Web: existing Dockerfile and migration predeploy. Worker: same repository/commit with Dockerfile builds, start command
`python manage.py crm_email_worker`, one replica, no predeploy command and no HTTP healthcheck.
Configure these settings on the Railway service; new Railway services no longer support legacy
Config as Code files. The worker shares DATABASE_URL and
CRM_EMAIL_* variables with web, runs one replica, and has no public domain or HTTP healthcheck.
It publishes a database heartbeat. The existing Redis/Celery configuration is not needed by
this integration. Disabling CRM_EMAIL_ENABLED pauses all outbound/background Google actions.

Run `python manage.py crm_email_readiness` on production. It reports only configuration readiness,
heartbeat, connection counts and queue counts. Never dump environment values or mailbox content.
Verify both services are running the exact merged commit, then perform a consenting two-account
pilot: compose, receive/reply in Gmail, observe CRM thread, scheduled send, cancellation, attachment,
disconnect and permission denial. Until that pilot succeeds, report deployed software separately
from activated/verified Google functionality.

An administrator can dry-run a retention request with
`python manage.py crm_email_purge --lead-id ID --before 2026-01-01T00:00:00+00:00`.
Add `--execute` only for the approved deletion. Entire eligible conversations are removed so
recovery cannot automatically reimport their messages. Queued/uncertain sends are excluded.

## Local verification
Use a disposable PostgreSQL database with `DATABASE_URL` set, install requirements, then run:
- `python manage.py check`
- `python manage.py makemigrations --check --dry-run`
- `python manage.py test apps.crm_email apps.crm apps.core.test_portal_navigation --noinput`
- `ruff check apps/crm_email` and `ruff format --check apps/crm_email`
- `mypy --config-file mypy-crm-email.ini` (requires mypy, django-stubs, types-requests)

The email feature is in SHARED_APPS only. Protected routes and worker reject tenant schemas.
Existing system notifications and newsletter delivery continue using their existing providers.

Activation also requires a stable, strong `DJANGO_SECRET_KEY`; the development fallback is rejected
by the Google connection checks. Changing an existing signing key invalidates existing sessions,
so coordinate that change during setup. Google secrets and mailbox access are not enabled merely
by deploying the feature.

## Verification recorded for this implementation
- Full Django suite: 334 tests passed on PostgreSQL after dependency security updates.
- Focused email suite includes 43 tests for isolation, OAuth identity/state, queue idempotency,
  uncertain sends, rate limits, revocation, attachments, incoming drafts exclusion, history recovery,
  watch renewal, concurrent push hints, signed import selection, templates and follow-up tasks.
- Strict mypy and Ruff checks cover the new email application.
- Browser QA covered settings/contact/compose/import at 1440px, 390px and 320px, plus saving a draft.
  The formatted-text composer was visually reviewed on desktop and mobile.
- Direct runtime dependency audit found no known vulnerabilities after patching existing Django,
  DRF and Pillow and choosing patched Google HTTP/encryption libraries and maintained nh3 sanitization.
- Google activation and the live two-account pilot require organization configuration and user consent.

## Production authentication prerequisite
Public demo access is disabled by default (`ENABLE_DEMO_ACCESS=0`). Deployment retires
public demo identities by disabling their accounts and invalidating their passwords; their
contacts, activities, and other records remain intact. Demo seeding cannot run unless an
operator explicitly enables demo access in an isolated environment. The email integration
refuses activation while demo access is enabled.

Before connecting a real mailbox, provision a private CRM-authorized user with a personal
password and matching Workspace email. Do not reuse the public demo administrator identity.
The one-use resource publisher setup flow can establish a private account; an authenticated
server operator must separately grant the intended CRM role after verifying its identity.

Google activation on 2026-09-14 used project `clearcode-crm`, internal OAuth audience,
`crm-gmail-events` topic and authenticated `crm-gmail-push` subscription. The project-level
managed sharing policy permits the ClearCodeReading organization and the single Gmail push
service identity. It replaces the legacy domain policy only for this project. The temporary
organization policy administrator grant was removed. A synthetic Google push was accepted
with HTTP 204; no real mailbox or outbound email was involved in that check.

## First-stage prelaunch pilot

CRM > Email settings > **First-stage email tests** (`/crm/email/first-stage/`) is
restricted to CRM administrators. The five individual September 2026 documents
supplied by Jamie are the approved copy source; they supersede the combined v1
copy. `first_stage_copy.json` preserves their first ENTRY subject and paragraphs.
No EXIT or later-stage automation is installed. Existing website receipts remain
separate intake acknowledgments.

The pilot is disabled by default. Administrators choose the connected primary
and equity sending mailboxes, complete signatures and Bethany's HTTPS scheduling
link, and enable testing. Every pilot message has a `[TEST]` subject and can only
be sent to Jamie's designated inbox, `info@clearcodereading.com`. There is no
customer-delivery mode. Missing names, company, investment category, calendar
link, or signature block the affected template rather than sending placeholders.
The equity signature can be supplied in pilot settings or taken from that sender's
saved CRM signature; Gmail API sending does not automatically insert a Gmail UI
signature. Mailbox credentials and normal user-owned drafts remain private.

The example buttons create clearly named test deals and contacts and exercise the
same entry signal as ordinary UI/API deal creation. A signed request token and a
locked pilot row make repeated submission of one example request idempotent.
Only new first-stage entries while testing is enabled are captured; enabling the
pilot does not backfill existing deals. A durable one-per-deal record prevents
resends on edits or re-entry. Bulk queryset updates deliberately do not generate
entry events; use the CRM UI/API to exercise the workflow.

The existing Gmail worker renders and queues captured events. It rechecks the
recipient, sender, deleted contact/deal, stage, and pilot status immediately before
sending. Pausing the pilot or leaving the first stage prevents unsent delivery.
Gmail uncertainty/reconciliation and provider retry handling remain in the shared
outbox. The test page shows queued, failed, cancelled, uncertain and Gmail-accepted
states separately; only inbox inspection establishes receipt. No historical deal
or customer is emailed by deployment.

Launch requires a separately authorized extension to the recipient policy and
review of website receipt overlap, consent, sender configuration and the final
calendar/signatures. Never remove the internal-recipient guard just to test a
customer address.

## Automated email wording

CRM > Email settings > **Automated emails** lists every email the system sends on its
own and lets CRM administrators edit each one. The registry in
`apps/crm_email/automated.py` describes each email once (key, trigger, recipient,
default wording, allowed placeholders). Senders call `copy_for(key)` at send time,
which returns the administrator's saved override (`AutomatedEmail` row) or the
default; `fill()` substitutes `{{placeholder}}` tokens. The editor rejects unknown
placeholders and multi-line subjects, records an `AuditLog` entry per save or
restore, and "Restore default" deletes the override. Covered emails:

- Website form confirmations to the visitor (`website_<kind>`: consultation,
  consultation booking, assessment, survey, career, newsletter, resources,
  support, website) and the shared team notice (`website_team`).
- Parent Reading Inventory emails: the suggested invitation wording (still
  editable per send), reminder, the three completion follow-ups, the owner
  review notice, and both consultation-booked messages.
- The five first-stage pipeline emails (`stage_<pipeline>`); the defaults still
  come from `first_stage_copy.json` and the pilot page links to each editor.
- The account invitation sent when a team member or portal user is created.

## Public consultation booking page
`/book/` (`consultation_booking`) is a public, no-index page listing the default consultation
host's confirmed, unbooked slots (the same `default_consultation_host()` and `available_slots()`
checks as the reading-inventory booking). A booking creates or updates the CRM contact, records a
consultation form submission flagged `consultation_booked` so the website receipt sends a
"Your consultation is booked" confirmation to the family and a team notice, stores a
`ConsultationBooking`, and moves the family enrollment deal to Consultation Scheduled. Public
and inventory bookings both take a slot out of availability, the CRM availability page and
the host's .ics feed. A honeypot field and a per-address cache rate limit protect the form.
When the first-stage pilot's scheduling link is blank, the Families email uses
`PUBLIC_APP_URL` + `/book/` (HTTPS only), so no link needs to be pasted after a domain change.
