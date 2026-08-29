import SwiftUI

struct SessionLogView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss
    let reader: ReaderSummary

    @State private var defaults: SessionDefaults?
    @State private var correct = 0
    @State private var attempted = 10
    @State private var duration = 60
    @State private var selectedActivities: Set<String> = []
    @State private var selectedErrors: Set<String> = []
    @State private var selectedBehaviors: Set<String> = []
    @State private var behavioralRating = "consistent"
    @State private var nextDirection = ""
    @State private var homePractice = ""
    @State private var notes = ""
    @State private var isLoading = true
    @State private var isSaving = false
    @State private var errorMessage: String?
    @State private var resultMessage: String?

    private var isValid: Bool {
        attempted > 0 && correct >= 0 && correct <= attempted && !selectedActivities.isEmpty
    }

    var body: some View {
        Form {
            if isLoading {
                HStack { Spacer(); ProgressView("Loading placement…"); Spacer() }
            } else if let errorMessage {
                Section {
                    StatusBanner(icon: "exclamationmark.triangle", title: "Cannot start this log", detail: errorMessage)
                    Button("Try again") { Task { await loadDefaults() } }
                }
            } else if let defaults {
                Section("Placement") {
                    LabeledContent("Reader", value: defaults.childName)
                    LabeledContent("Method", value: defaults.curriculumName)
                    LabeledContent("Position", value: "\(defaults.positionCode) · \(defaults.positionTitle)")
                    LabeledContent("Session", value: defaults.interventionPartLabel)
                }

                Section("Accuracy") {
                    Stepper("Correct: \(correct)", value: $correct, in: 0...attempted)
                        .accessibilityIdentifier("session.correct")
                    Stepper("Attempted: \(attempted)", value: $attempted, in: max(correct, 1)...200)
                        .accessibilityIdentifier("session.attempted")
                    LabeledContent("Accuracy") {
                        Text(attempted == 0 ? "—" : "\((Double(correct) / Double(attempted) * 100).formatted(.number.precision(.fractionLength(0...1))))%")
                            .fontWeight(.semibold).foregroundStyle(Brand.deepTeal)
                    }
                    Stepper("Duration: \(duration) minutes", value: $duration, in: 5...240, step: 5)
                }

                Section("Activities") {
                    ForEach(defaults.suggestedActivities) { option in
                        Toggle(option.label, isOn: binding(for: option.code, in: $selectedActivities))
                    }
                }

                if !defaults.errorPatternOptions.isEmpty {
                    Section("Observed error patterns") {
                        ForEach(defaults.errorPatternOptions) { option in
                            Toggle(option.label, isOn: binding(for: option.code, in: $selectedErrors))
                        }
                    }
                }

                if !defaults.behavioralObservationOptions.isEmpty {
                    Section("Observable behaviors") {
                        ForEach(defaults.behavioralObservationOptions) { option in
                            Toggle(option.label, isOn: binding(for: option.code, in: $selectedBehaviors))
                        }
                        if !selectedBehaviors.isEmpty {
                            Picker("Rating", selection: $behavioralRating) {
                                ForEach(defaults.behavioralRatingOptions) { option in
                                    Text(option.label).tag(option.code)
                                }
                            }
                        }
                    }
                }

                Section {
                    TextField("Next-session direction", text: $nextDirection, axis: .vertical)
                        .lineLimit(2...5)
                    TextField("Home practice", text: $homePractice, axis: .vertical)
                        .lineLimit(2...5)
                    TextField("Private session notes", text: $notes, axis: .vertical)
                        .lineLimit(2...6)
                } header: {
                    Text("Editable guidance")
                } footer: {
                    Text("Suggested guidance is advisory. Review and edit it before saving.")
                }

                Section {
                    Button {
                        Task { await save() }
                    } label: {
                        HStack {
                            Spacer()
                            if isSaving { ProgressView().tint(.white) }
                            Text(isSaving ? "Saving…" : "Complete Session").fontWeight(.semibold)
                            Spacer()
                        }
                    }
                    .listRowBackground(Brand.deepTeal)
                    .foregroundStyle(.white)
                    .disabled(!isValid || isSaving)
                    .accessibilityIdentifier("session.submit")
                }
            }
        }
        .navigationTitle("Log Session")
        .navigationBarTitleDisplayMode(.inline)
        .task { await loadDefaults() }
        .alert("Session log", isPresented: Binding(
            get: { resultMessage != nil },
            set: { if !$0 { resultMessage = nil } }
        )) {
            Button("Done") { dismiss() }
        } message: {
            Text(resultMessage ?? "")
        }
    }

    private func loadDefaults() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let value = try await appState.api.sessionDefaults(childID: reader.id)
            defaults = value
            selectedActivities = Set(value.suggestedActivities.map(\.code))
            nextDirection = value.nextSessionDirection
            homePractice = value.homePracticeSuggestion
            behavioralRating = value.behavioralRatingOptions.first?.code ?? "consistent"
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func save() async {
        guard isValid else { return }
        isSaving = true
        defer { isSaving = false }
        let request = RapidSessionRequest(
            clientRequestId: UUID(),
            child: reader.id,
            accuracyNumerator: correct,
            accuracyDenominator: attempted,
            durationMinutes: duration,
            scheduledStart: nil,
            activityCodes: selectedActivities.sorted(),
            errorPatternCodes: selectedErrors.sorted(),
            behavioralObservationCodes: selectedBehaviors.sorted(),
            behavioralRating: behavioralRating,
            nextSessionDirection: nextDirection,
            homePracticeSuggestion: homePractice,
            notes: notes
        )
        do {
            switch try await appState.submit(request) {
            case .saved:
                resultMessage = "The completed session was saved to ClearCode."
            case .queued:
                resultMessage = "The session is safely queued and will retry when ClearCode reconnects."
            }
        } catch {
            appState.alertMessage = error.localizedDescription
        }
    }

    private func binding(for value: String, in set: Binding<Set<String>>) -> Binding<Bool> {
        Binding(
            get: { set.wrappedValue.contains(value) },
            set: { enabled in
                if enabled { set.wrappedValue.insert(value) }
                else { set.wrappedValue.remove(value) }
            }
        )
    }
}
