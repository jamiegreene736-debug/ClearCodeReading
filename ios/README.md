# ClearCode iOS

Generate the deterministic Xcode project and open it:

```bash
cd ios
xcodegen generate
open ClearCode.xcodeproj
```

Debug and Release builds default to
`https://clearcodereading-production.up.railway.app` so simulator and physical-device
testing use the same reachable API. When running Django locally in the simulator,
override `API_BASE_URL` with `http://127.0.0.1:8000` in an uncommitted local Xcode
configuration. Never use a loopback address for a physical-device build because it
points to the iPhone or iPad rather than the developer's Mac.

Build and test without signing:

```bash
xcodebuild -project ClearCode.xcodeproj -scheme ClearCode \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  CODE_SIGNING_ALLOWED=NO test
```

See `docs/IOS_APP_V1.md` for product scope, privacy boundaries, and release gates.
