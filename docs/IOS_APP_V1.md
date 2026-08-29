# ClearCode Reading iOS v1

## Product scope

The native app is a focused companion to the ClearCode platform. The server remains the
authority for identity, center membership, consent, reader visibility, instructional
placement, and reporting permissions.

Version 1 provides:

- email/password JWT authentication with refresh-token rotation;
- a role-aware home and reader directory;
- today's specialist sessions and placement-backed rapid session logging;
- retry-safe offline session submission using a client-generated UUID;
- consent-scoped family progress, recent mastery, practice guidance, and trends;
- leadership-only de-identified outcome snapshots;
- installation registration, optional APNs-token registration, and audited logout;
- Keychain token storage and file-protected cached/pending operational data.

CRM administration, workforce payments, consent mutation, child-data editing, and
scheduling approval stay in the authenticated web portal for v1. The app links those
decisions to server-provided capabilities and never grants itself access.

## Supported environment

- iOS 17 or newer
- SwiftUI with no third-party runtime dependencies
- Debug API: `http://127.0.0.1:8000`
- Release API: `https://clearcodereading.com`
- Bundle identifier: `com.clearcodereading.ios`

The API base URL is a build setting and may be overridden without changing source code.

## Security and privacy boundary

- Access and refresh tokens use a ThisDeviceOnly Keychain accessibility class.
- Cached bootstrap data and queued logs use complete file protection.
- Queued writes contain only the requested operational session payload and are removed
  after a successful server acknowledgement.
- A server-generated 401 triggers one refresh attempt; refresh failure clears the local
  session and requires sign-in.
- Reader visibility, progress, outcomes, and session permissions are evaluated again by
  Django on every request.
- The app includes no advertising SDK, analytics SDK, or cross-app tracking.

## Release gates

Before TestFlight or App Store submission:

1. Set the Apple Development Team and create the matching App ID.
2. Enable Push Notifications for that App ID and create development/production APNs
   credentials for the server when notifications are enabled.
3. Confirm `https://clearcodereading.com/api/v1/health/` and mobile endpoints are live.
4. Apply shared and tenant migrations before allowing mobile sign-ins.
5. Replace demo credentials and confirm production password policy/rate limiting.
6. Complete App Store privacy labels, support URL, screenshots, age rating, and review notes.
7. Archive the Release configuration, validate it in Xcode Organizer, and upload to
   TestFlight for role-by-role acceptance testing.

No Apple team identifiers, signing certificates, APNs keys, or production secrets belong
in this repository.
