# User invitations and employee Gmail onboarding

Administrators use **Manage → Add user** (`/portal/users/`) to invite backend employees, teachers, or parents. **Manage → User invitations** opens delivery receipts and resend controls. CRM Team also supports employee invitations. The older parent/teacher creation endpoint uses the same invitation flow.

Backend employee maps to the existing least-privilege `CRM_USER` role. It does not grant staff, superuser, or teacher-hiring access. Hiring access remains a separate Team control. School administrators can invite only teachers/parents and see/resend only invitations they created. Super administrators manage all invitations. No account is granted administrator access by this form.

## Password setup

New accounts have unusable passwords until the recipient follows the emailed setup link and chooses a password. Email contains their name, email/login, role, setup link, and regular login link. No raw password appears in email, admin messages, or CRM history.

The setup view reuses Django's `PasswordResetConfirmView` and configured password validators. Its invitation-specific token includes a per-invitation nonce, expires according to `PASSWORD_RESET_TIMEOUT` (Django default: 72 hours), and is invalidated by password changes, acceptance, login, and resending. The view also rejects inactive/deleted users and rechecks the token under a database lock before setting the password. Opening a link does not consume it; successful password selection does. The user then logs in normally.

## Delivery and recovery

Production sends privately through the inviting administrator's connected Gmail mailbox. This does not create a shared CRM Message or contact, because setup credentials must not appear in shared conversation history. Gmail's existing configuration, permissions, encrypted credentials, mailbox lock, and bounded request timeouts apply. SMTP is supported when Gmail integration is disabled; console/dummy/file/in-memory delivery is rejected outside explicitly enabled tests.

`UserInvitation` persists pending/sending/sent/failed/uncertain status and request, send, and acceptance timestamps. Sent means provider acceptance, not inbox delivery. A timeout or ambiguous provider response is uncertain and is never automatically retried. Operators inspect sent mail, then explicitly resend if appropriate. Resend is POST/CSRF-protected, replaces the old token, and enforces a two-minute cooldown. An interrupted send can be recovered the same way after the cooldown. Recipients whose setup is complete cannot receive another setup invitation through this control.

## First login

A backend user with no previous login and no connected mailbox lands on **Welcome — connect your work Gmail**, even if the login supplied a `next` destination. Teachers, parents, and students are excluded even if they have another access flag. Users explicitly choose Connect Gmail, invoking the existing Google OAuth flow; they can continue to their original safe destination and connect later through CRM Email settings. Google cancellation/failure retains the existing retry flow. Unconfigured organizations show a clear setup-required state and do not trap users at onboarding.

## Research and validation

- [Django 5.2 authentication views](https://docs.djangoproject.com/en/5.2/topics/auth/default/#django.contrib.auth.views.PasswordResetConfirmView): reuse token validation and password setup rather than distribute temporary passwords.
- [Google web-server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server): reuse the existing explicit consent and server-side OAuth connection.
- `apps/users/test_onboarding.py` covers role boundaries, CSRF, delivery failures/uncertainty, private Gmail sends, expiration, one-time use, resend revocation (including already-open browser sessions), and first-login routing.

### HTTPS browser submissions

The onboarding layout uses `Referrer-Policy: same-origin` through its meta tag. Do not change this to `no-referrer`: browsers then send `Origin: null` on native form POSTs, and Django correctly rejects them even when the CSRF token is valid. Same-origin retains the headers needed for local forms and suppresses the referrer on external navigation. CSRF origin and token validation remain enabled.

`python scripts/test_onboarding_browser.py` (Playwright Chromium/WebKit installed, migrated local database configured through `POSTGRES_*`, no `DATABASE_URL`) captures real browser form submissions and replays them through Django with CSRF checks enforced. It exercises account creation, invitation email, and password setup, checks external navigation privacy, rolls back all database changes, and captures email in memory. Django regression tests also reject null/foreign origins and cover browsers that send only a same-site Referer.

Reference: [Django CSRF documentation, removing the Referer header](https://docs.djangoproject.com/en/5.2/ref/csrf/#how-it-works).
