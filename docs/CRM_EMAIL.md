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
Web: existing Dockerfile and migration predeploy. Worker: same repository/commit, using
`railway-email-worker.json` as its Railway config file. The worker shares DATABASE_URL and
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
- Focused email suite includes 42 tests for isolation, OAuth identity/state, queue idempotency,
  uncertain sends, rate limits, revocation, attachments, incoming drafts exclusion, history recovery,
  watch renewal, concurrent push hints, signed import selection, templates and follow-up tasks.
- Strict mypy and Ruff checks cover the new email application.
- Browser QA covered settings/contact/compose/import at 1440px, 390px and 320px, plus saving a draft.
  The formatted-text composer was visually reviewed on desktop and mobile.
- Direct runtime dependency audit found no known vulnerabilities after patching existing Django,
  DRF and Pillow and choosing patched Google HTTP/encryption libraries and maintained nh3 sanitization.
- Google activation and the live two-account pilot require organization configuration and user consent.
