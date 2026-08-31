import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.openURL) private var openURL
    @State private var confirmsDiscardingPendingLogs = false

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
                switch appState.notificationAuthorizationState {
                case .authorized, .provisional, .ephemeral:
                    Label("Notifications enabled", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                case .denied:
                    Button {
                        openNotificationSettings()
                    } label: {
                        Label("Open notification settings", systemImage: "gear")
                    }
                case .notDetermined, .unknown:
                    Button {
                        Task { await appState.requestPushNotifications() }
                    } label: {
                        Label("Enable notifications", systemImage: "bell.badge.fill")
                    }
                }
                Text(notificationHelpText)
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
                    if appState.pendingLogs.isEmpty {
                        Task { await appState.signOut() }
                    } else {
                        confirmsDiscardingPendingLogs = true
                    }
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
        .task {
            await appState.refreshNotificationAuthorization()
        }
        .confirmationDialog(
            "Discard unsent session logs?",
            isPresented: $confirmsDiscardingPendingLogs,
            titleVisibility: .visible
        ) {
            Button("Discard Logs and Sign Out", role: .destructive) {
                Task { await appState.signOut() }
            }
            Button("Keep Working", role: .cancel) {}
        } message: {
            Text(
                "\(appState.pendingLogs.count) unsent session "
                    + (appState.pendingLogs.count == 1 ? "log will" : "logs will")
                    + " be removed from this device. Retry them before signing out if they must be saved."
            )
        }
    }

    private var notificationHelpText: String {
        switch appState.notificationAuthorizationState {
        case .authorized, .provisional, .ephemeral:
            "Alerts are allowed for this device. ClearCode registers only this installation and its Apple push token."
        case .denied:
            "Notifications are turned off for ClearCode. Open iOS Settings to allow alerts."
        case .notDetermined, .unknown:
            "Enable alerts for important ClearCode updates on this device."
        }
    }

    private func openNotificationSettings() {
        guard let url = URL(string: UIApplication.openNotificationSettingsURLString) else { return }
        openURL(url)
    }
}
