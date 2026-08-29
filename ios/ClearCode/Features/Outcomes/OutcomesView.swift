import SwiftUI

struct OutcomesView: View {
    @EnvironmentObject private var appState: AppState
    @State private var snapshots: [OutcomeSnapshot] = []
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        List {
            Section {
                StatusBanner(
                    icon: "lock.shield.fill",
                    title: "De-identified reporting",
                    detail: "Only cohorts meeting the server's privacy floor appear here.",
                    color: Brand.deepTeal
                )
                .listRowInsets(EdgeInsets())
                .listRowBackground(Color.clear)
            }

            if isLoading {
                HStack { Spacer(); ProgressView(); Spacer() }
            } else if let errorMessage {
                StatusBanner(icon: "exclamationmark.triangle", title: "Outcomes unavailable", detail: errorMessage)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            } else if snapshots.isEmpty {
                ContentUnavailableView(
                    "No reportable cohorts",
                    systemImage: "chart.line.uptrend.xyaxis",
                    description: Text("Snapshots appear after aggregation and only when the privacy threshold is met.")
                )
            } else {
                ForEach(snapshots) { snapshot in
                    Section("\(snapshot.methodology.clearCodeLabel) · \(snapshot.gradeBand.clearCodeLabel)") {
                        LabeledContent("Window", value: "\(snapshot.windowStart) – \(snapshot.windowEnd)")
                        LabeledContent("Privacy floor", value: "\(snapshot.privacyFloor)")
                        ForEach(metricRows(snapshot), id: \.0) { name, value in
                            LabeledContent(name, value: value)
                        }
                    }
                }
            }
        }
        .navigationTitle("Outcomes")
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            snapshots = try await appState.api.outcomes()
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func metricRows(_ snapshot: OutcomeSnapshot) -> [(String, String)] {
        let preferred = [
            "cohort_students", "completed_sessions", "skill_mastery_rate",
            "weighted_accuracy_rate", "mean_sessions_to_mastery",
        ]
        return preferred.compactMap { key in
            guard let value = snapshot.metrics[key] else { return nil }
            let suffix = key.contains("rate") ? "%" : ""
            return (key.clearCodeLabel, value.displayText + suffix)
        }
    }
}
