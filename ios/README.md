# ClearCode iOS

Generate the deterministic Xcode project and open it:

```bash
cd ios
xcodegen generate
open ClearCode.xcodeproj
```

The Debug build talks to `http://127.0.0.1:8000`; the Release build talks to
`https://clearcodereading-production.up.railway.app`. Override `API_BASE_URL` in an uncommitted local Xcode
configuration when testing another environment.

Build and test without signing:

```bash
xcodebuild -project ClearCode.xcodeproj -scheme ClearCode \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  CODE_SIGNING_ALLOWED=NO test
```

See `docs/IOS_APP_V1.md` for product scope, privacy boundaries, and release gates.
