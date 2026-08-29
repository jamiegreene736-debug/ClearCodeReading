import SwiftUI

struct TodaySessionsView: View {
    @EnvironmentObject private var appState: AppState
    @State private var sessions: [InterventionSession] = []
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        List {
            if let errorMessage {
                StatusBanner(icon: "exclamationmark.triangle", title: "Sessions unavailable", detail: errorMessage)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            }
            if isLoading {
                HStack { Spacer(); ProgressView(); Spacer() }
            } else if sessions.isEmpty {
                ContentUnavailableView(
                    "No sessions today",
                    systemImage: "calendar",
                    description: Text("Choose a reader to log an unscheduled session, or check the web portal for scheduling changes.")
                )
            } else {
                ForEach(sessions) { session in
                    VStack(alignment: .leading, spacing: 7) {
                        HStack {
                            Text(session.childName).font(.headline)
                            Spacer()
                            Text(session.status.clearCodeLabel)
                                .font(.caption.bold())
                                .padding(.horizontal, 8).padding(.vertical, 4)
                                .background(statusColor(session.status).opacity(0.15), in: Capsule())
                                .foregroundStyle(statusColor(session.status))
                        }
                        Text(session.scheduledStart.clearCodeDate)
                            .font(.subheadline).foregroundStyle(.secondary)
                        if !session.positionCode.isEmpty {
                            Text("\(session.positionCode) · \(session.interventionPart.clearCodeLabel)")
                                .font(.subheadline)
                        }
                        if let accuracy = session.accuracyRate?.value {
                            Label("\(accuracy.formatted(.number.precision(.fractionLength(0...1))))% accuracy", systemImage: "target")
                                .font(.subheadline).foregroundStyle(Brand.deepTeal)
                        }
                    }
                    .padding(.vertical, 5)
                    .accessibilityElement(children: .combine)
                }
            }
        }
        .navigationTitle("Today")
        .task { await load() }
        .refreshable { await load() }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink {
                    ReadersView()
                } label: {
                    Label("Choose reader", systemImage: "plus")
                }
            }
        }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            sessions = try await appState.api.todaySessions()
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "completed": Brand.deepTeal
        case "cancelled", "no_show": .red
        default: Brand.forest
        }
    }
}
