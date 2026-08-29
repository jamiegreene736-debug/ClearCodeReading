import SwiftUI

@main
struct ClearCodeApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            AppRootView()
                .environmentObject(appState)
                .task { await appState.start() }
                .onReceive(NotificationCenter.default.publisher(for: .clearCodePushToken)) { notification in
                    guard let token = notification.object as? String else { return }
                    Task { await appState.receivedPushToken(token) }
                }
                .alert(
                    "ClearCode Reading",
                    isPresented: Binding(
                        get: { appState.alertMessage != nil },
                        set: { if !$0 { appState.alertMessage = nil } }
                    )
                ) {
                    Button("OK", role: .cancel) { appState.alertMessage = nil }
                } message: {
                    Text(appState.alertMessage ?? "")
                }
        }
        .onChange(of: scenePhase) { _, newPhase in
            guard newPhase == .active, appState.phase == .signedIn else { return }
            Task { await appState.refresh() }
        }
    }
}
