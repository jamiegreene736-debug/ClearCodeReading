# Mobile API Contract

The native iOS client uses the existing `/api/v1/` JWT API plus a small mobile bootstrap and device lifecycle contract.

## Endpoints

- `POST /api/v1/auth/token/`: authenticate with email and password.
- `POST /api/v1/auth/token/refresh/`: rotate or refresh an access token.
- `GET /api/v1/mobile/bootstrap/`: return the authenticated user's minimal identity, center memberships, visible readers, and server-authoritative capabilities.
- `POST /api/v1/mobile/devices/`: idempotently register an installation and optional APNs token.
- `POST /api/v1/mobile/logout/`: deactivate the installation and write an audit event.
- `GET /api/v1/sessions/defaults/?child=<id>`: return placement-backed rapid-log defaults.
- `POST /api/v1/sessions/rapid-log/`: create a session with an idempotent `client_request_id`.
- `GET /api/v1/sessions/today/`: return the specialist's sessions for the current day.
- `GET /api/v1/progress/dashboard/?child=<id>`: return the authorized family progress summary.
- `GET /api/v1/outcomes/snapshots/`: return leadership-only de-identified aggregates.

## Privacy and tenancy

Bootstrap reader visibility is derived from role, active center membership, and guardian consent. The client cannot grant itself a capability. Guardian progress remains relationship- and consent-scoped. Leadership outcomes retain the existing privacy floor and contain no child or specialist identity.

Device records contain installation metadata, not child data. Rapid-log retries use a UUID generated on device so reconnects cannot create duplicate sessions.

## Rollout

Apply migrations before enabling mobile clients:

```bash
python manage.py migrate
```

No additional specialist data collection is introduced. Existing web and parent workflows continue to use the same operational models and permission checks.
