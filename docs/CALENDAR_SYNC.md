# Individual consultation calendars

Each CRM host opens **My calendar** (`/crm/calendar/`) to connect their own
Google Calendar secret iCal address or Apple/iCloud published calendar URL.
No Gmail mailbox permissions or Google OAuth setup are required. Only one source
calendar is connected per host. Account credentials are never requested.

## Behavior

- Public host eligibility remains controlled by the existing consultation host
  selection policy (currently the configured default host, Bethany). Connecting
  a calendar does not make other profiles publicly bookable.
- Hosts continue to add/confirm the consultation times they wish to offer.
- Families see up to 100 candidate times within 90 days. Connected calendars are
  read on each availability request and rechecked immediately before booking.
- Busy recurring and all-day events suppress overlaps. Cancelled and transparent
  events do not block time. The selected calendar time zone governs floating and
  all-day events. Source event details are not persisted or shown to others.
- Failed reads fail closed for the connected host and record a safe status on
  My calendar. Disconnecting returns the host to manual availability.
- A separate private subscription publishes that host's bookings, with stable
  UIDs matching invitation emails. It omits family and contact details. Hosts
  may replace the token to revoke an old subscription link.
- Subscriptions are read-only. Changes in an external calendar do not reschedule
  CRM bookings. Provider publishing/refresh delays apply; this is not atomic
  cross-provider scheduling or push-based two-way synchronization.

## Privacy and operation

Incoming URLs are encrypted using a domain-separated key derived from Django's
SECRET_KEY. Retain old keys in SECRET_KEY_FALLBACKS during rotation until hosts
reconnect. Only the authenticated host can change their connection, including
when other host IDs are submitted. An iCloud published calendar can be read by
anyone holding its URL; the connection page explicitly explains this.

Only Google Calendar `/calendar/ical/` and Apple's numbered
`pN-calendars.icloud.com/published/` HTTPS endpoints are accepted. Redirects,
credentials, and nonstandard ports are rejected. Responses are bounded to 2 MiB,
2,000 source events, and a 90-day query horizon. High-frequency recurrence rules
are rejected rather than silently ignored. Subscription URLs are bearer secrets;
keep access logs private and redact `/calendars/` URL paths from analytics.

## Verification and rollout

Apply migration `crm.0014_hostcalendar` through the normal deployment migration
step. No source calendars are connected automatically. Test parsing, recurrence,
account isolation, provider errors, booking-time rechecks, and feed revocation:

    python manage.py test apps.crm.test_calendars apps.crm.test_inventory

Run the full suite, Django checks, migration drift check, Ruff on the calendar
modules, and strict mypy on those modules before merging. Verify the exact merged
commit in Railway and the authenticated calendar settings route after deployment.

Provider references:
- https://support.google.com/calendar/answer/37648
- https://support.apple.com/guide/icloud/share-a-calendar-mm6b1a9479/icloud
- https://recurring-ical-events.readthedocs.io/en/v3.8.0/reference/api.html
