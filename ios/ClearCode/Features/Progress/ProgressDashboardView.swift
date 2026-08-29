import Charts
import SwiftUI

struct ProgressDashboardView: View {
    @EnvironmentObject private var appState: AppState
    let reader: ReaderSummary

    @State private var dashboard: ProgressDashboard?
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if isLoading {
                    ProgressView("Loading progress…").frame(maxWidth: .infinity).padding(.top, 48)
                } else if let errorMessage {
                    StatusBanner(icon: "exclamationmark.shield", title: "Progress unavailable", detail: errorMessage)
                    Button("Try again") { Task { await load() } }
                        .buttonStyle(.borderedProminent).tint(Brand.deepTeal)
                } else if let dashboard {
                    SummaryGrid(summary: dashboard.summary)

                    if !dashboard.progressOverTime.isEmpty {
                        BrandCard {
                            Text("Accuracy over time").font(.headline)
                            Chart(dashboard.progressOverTime) { point in
                                if let accuracy = point.accuracy?.value {
                                    LineMark(
                                        x: .value("Session", point.date),
                                        y: .value("Accuracy", accuracy)
                                    )
                                    .foregroundStyle(Brand.deepTeal)
                                    PointMark(
                                        x: .value("Session", point.date),
                                        y: .value("Accuracy", accuracy)
                                    )
                                    .foregroundStyle(Brand.deepTeal)
                                }
                            }
                            .chartYScale(domain: 0...100)
                            .frame(height: 210)
                            .accessibilityLabel("Session accuracy over time")
                        }
                    }

                    if !dashboard.specialistNote.isEmpty || !dashboard.homePractice.isEmpty {
                        BrandCard {
                            VStack(alignment: .leading, spacing: 12) {
                                if !dashboard.specialistNote.isEmpty {
                                    Label("Specialist note", systemImage: "quote.bubble.fill").font(.headline)
                                    Text(dashboard.specialistNote)
                                    if !dashboard.specialistName.isEmpty {
                                        Text("— \(dashboard.specialistName)").font(.footnote).foregroundStyle(.secondary)
                                    }
                                }
                                if !dashboard.homePractice.isEmpty {
                                    Divider()
                                    Label("Home practice", systemImage: "house.fill").font(.headline)
                                    Text(dashboard.homePractice)
                                }
                            }
                        }
                    }

                    BrandCard {
                        VStack(alignment: .leading, spacing: 8) {
                            Label("Milestone", systemImage: "flag.checkered").font(.headline)
                            Text(dashboard.milestone.label).fontWeight(.semibold)
                            if let position = dashboard.milestone.currentPosition {
                                Text("Current position: \(position)")
                            }
                            if let weeks = dashboard.milestone.estimatedWeeks {
                                Text("Estimated timing: about \(weeks) \(weeks == 1 ? "week" : "weeks")")
                            }
                            if let disclaimer = dashboard.milestone.disclaimer {
                                Text(disclaimer).font(.footnote).foregroundStyle(.secondary)
                            }
                        }
                    }

                    if !dashboard.skills.isEmpty {
                        Text("Foundational skills").font(.headline)
                        ForEach(dashboard.skills) { skill in
                            BrandCard {
                                HStack {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(skill.name).fontWeight(.semibold)
                                        Text(skill.domain.clearCodeLabel).font(.subheadline).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Text(skill.status.clearCodeLabel)
                                        .font(.caption.bold())
                                        .padding(.horizontal, 8).padding(.vertical, 4)
                                        .background(Brand.seafoam.opacity(0.45), in: Capsule())
                                }
                            }
                        }
                    }

                    if !dashboard.recentMastery.isEmpty {
                        Text("Recent mastery").font(.headline)
                        ForEach(dashboard.recentMastery) { mastery in
                            Label {
                                VStack(alignment: .leading) {
                                    Text(mastery.skill).fontWeight(.semibold)
                                    Text(mastery.masteredAt.clearCodeDate).font(.subheadline).foregroundStyle(.secondary)
                                }
                            } icon: {
                                Image(systemName: "star.fill").foregroundStyle(Brand.gold)
                            }
                            .padding(.vertical, 4)
                        }
                    }

                    Text("Foundational literacy information only. Progress estimates are advisory and are not diagnoses or guarantees.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .padding()
        }
        .background(Brand.linen.opacity(0.55))
        .navigationTitle("\(reader.firstName)'s Progress")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            dashboard = try await appState.api.progress(childID: reader.id)
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct SummaryGrid: View {
    let summary: ProgressSummary

    var body: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
            ProgressMetric(value: "\(summary.completedSessions)", label: "Sessions")
            ProgressMetric(value: "\(summary.masteredSkills)", label: "Mastered skills")
            ProgressMetric(value: "\(summary.trackedSkills)", label: "Tracked skills")
            ProgressMetric(
                value: summary.latestAccuracy.map { "\($0.value.formatted(.number.precision(.fractionLength(0...1))))%" } ?? "—",
                label: "Latest accuracy"
            )
        }
    }
}

private struct ProgressMetric: View {
    let value: String
    let label: String

    var body: some View {
        VStack(spacing: 4) {
            Text(value).font(.title2.bold()).foregroundStyle(Brand.deepTeal)
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 86)
        .background(.background, in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .combine)
    }
}
