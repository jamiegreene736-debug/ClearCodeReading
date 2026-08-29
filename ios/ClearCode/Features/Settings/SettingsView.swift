import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState

    private var version: String {
        let short = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "—"
        return "\(short) (\(build))"
    }

    var body: some View {
        List {
            if let user = appState.bootstrap?.user {
                Section("Account") {
                    LabeledContent("Name", value: user.displayName)
                    LabeledContent("Email", value: user.email)
                    LabeledContent("Role", value: user.role.rawValue.clearCodeLabel)
                }
            }

            Section("Notifications") {
                Button {
                    Task { await appState.requestPushNotifications() }
                } label: {
                    Label("Enable notifications", systemImage: "bell.badge.fill")
                }
                Text("ClearCode registers only this installation and its Apple push token. Notification delivery requires server-side APNs configuration.")
                    .font(.footnote).foregroundStyle(.secondary)
            }

            Section("Offline session logs") {
                LabeledContent("Waiting", value: "\(appState.pendingLogs.count)")
                LabeledContent("Needs review", value: "\(appState.pendingLogs.filter(\.needsReview).count)")
                if !appState.pendingLogs.isEmpty {
                    Button("Retry now") {
                        Task { await appState.flushPendingLogs(includeNeedsReview: true) }
                    }
                }
                ForEach(appState.pendingLogs.filter(\.needsReview)) { item in
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Reader #\(item.request.child) · \(item.queuedAt.formatted())")
                            .font(.subheadline).fontWeight(.semibold)
                        Text(item.failureMessage ?? "Needs review")
                            .font(.footnote).foregroundStyle(.red)
                    }
                }
            }

            Section("About") {
                LabeledContent("Version", value: version)
                Link("Privacy policy", destination: URL(string: "https://clearcodereading-production.up.railway.app/privacy/")!)
                Link("Open web portal", destination: URL(string: "https://clearcodereading-production.up.railway.app/login/")!)
            }

            Section {
                Button(role: .destructive) {
                    Task { await appState.signOut() }
                } label: {
                    HStack {
                        Spacer()
                        if appState.isBusy { ProgressView() }
                        Text("Sign Out")
                        Spacer()
                    }
                }
                .disabled(appState.isBusy)
                .accessibilityIdentifier("settings.signout")
            }
        }
        .navigationTitle("Settings")
    }
}
