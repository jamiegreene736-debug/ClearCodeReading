import SwiftUI

struct HomeView: View {
    @EnvironmentObject private var appState: AppState

    private var user: AppUser? { appState.bootstrap?.user }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if appState.isOffline {
                    StatusBanner(
                        icon: "wifi.slash",
                        title: "Offline mode",
                        detail: "Saved information is shown. New session logs will be queued safely."
                    )
                }
                if !appState.pendingLogs.isEmpty {
                    StatusBanner(
                        icon: "arrow.triangle.2.circlepath",
                        title: "\(appState.pendingLogs.count) session \(appState.pendingLogs.count == 1 ? "log" : "logs") waiting",
                        detail: appState.pendingLogs.contains(where: \.needsReview)
                            ? "At least one log needs review before it can be accepted."
                            : "ClearCode will retry automatically when the server is available.",
                        color: appState.pendingLogs.contains(where: \.needsReview) ? .orange : Brand.deepTeal
                    )
                }

                BrandCard {
                    HStack(spacing: 14) {
                        Image("BrandMonogram")
                            .resizable()
                            .scaledToFit()
                            .frame(width: 58, height: 58)
                            .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("Welcome, \(user?.firstName.isEmpty == false ? user!.firstName : user?.displayName ?? "Reader")")
                                .font(.title2.bold())
                                .foregroundStyle(Brand.ink)
                            Text(user?.role.rawValue.clearCodeLabel ?? "")
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                if let memberships = appState.bootstrap?.memberships, !memberships.isEmpty {
                    Text("Centers").font(.headline)
                    ForEach(memberships) { membership in
                        BrandCard {
                            Label {
                                VStack(alignment: .leading) {
                                    Text(membership.centerName).fontWeight(.semibold)
                                    Text(membership.title.isEmpty ? membership.role.clearCodeLabel : membership.title)
                                        .font(.subheadline).foregroundStyle(.secondary)
                                }
                            } icon: {
                                Image(systemName: "building.2.fill").foregroundStyle(Brand.deepTeal)
                            }
                        }
                    }
                }

                Text("Your workspace").font(.headline)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 145), spacing: 12)], spacing: 12) {
                    MetricTile(value: "\(appState.bootstrap?.children.count ?? 0)", label: "Visible readers", icon: "person.2")
                    if appState.capabilities?.logSessions == true {
                        MetricTile(value: "Ready", label: "Session logging", icon: "checkmark.circle")
                    }
                    if appState.capabilities?.viewProgress == true {
                        MetricTile(value: "Live", label: "Family progress", icon: "chart.xyaxis.line")
                    }
                    if appState.capabilities?.viewOutcomes == true {
                        MetricTile(value: "Private", label: "Outcome cohorts", icon: "lock.shield")
                    }
                }
            }
            .padding()
        }
        .background(Brand.linen.opacity(0.55))
        .navigationTitle("ClearCode")
        .refreshable { await appState.refresh() }
    }
}

private struct MetricTile: View {
    let value: String
    let label: String
    let icon: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: icon).foregroundStyle(Brand.deepTeal).font(.title3)
            Text(value).font(.title3.bold()).foregroundStyle(Brand.ink)
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 112, alignment: .leading)
        .padding()
        .background(.background, in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .combine)
    }
}
