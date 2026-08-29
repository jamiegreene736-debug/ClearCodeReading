import SwiftUI

struct ReadersView: View {
    @EnvironmentObject private var appState: AppState
    @State private var searchText = ""

    private var readers: [ReaderSummary] {
        let values = appState.bootstrap?.children ?? []
        guard !searchText.isEmpty else { return values }
        return values.filter {
            $0.displayName.localizedCaseInsensitiveContains(searchText)
                || $0.centerName.localizedCaseInsensitiveContains(searchText)
        }
    }

    var body: some View {
        List {
            if readers.isEmpty {
                ContentUnavailableView(
                    searchText.isEmpty ? "No readers available" : "No matching readers",
                    systemImage: "person.2.slash",
                    description: Text(searchText.isEmpty
                        ? "Reader access is assigned by your center or guardian relationship."
                        : "Try a different name or center.")
                )
                .listRowBackground(Color.clear)
            } else {
                ForEach(readers) { reader in
                    NavigationLink(value: reader) {
                        ReaderRow(reader: reader)
                    }
                }
            }
        }
        .navigationTitle("Readers")
        .searchable(text: $searchText, prompt: "Name or center")
        .navigationDestination(for: ReaderSummary.self) { reader in
            ReaderDetailView(reader: reader)
        }
        .refreshable { await appState.refresh() }
    }
}

private struct ReaderRow: View {
    let reader: ReaderSummary

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle().fill(Brand.seafoam.opacity(0.45))
                Text(String(reader.firstName.prefix(1)).uppercased())
                    .font(.headline).foregroundStyle(Brand.forest)
            }
            .frame(width: 44, height: 44)
            VStack(alignment: .leading, spacing: 3) {
                Text(reader.displayName).fontWeight(.semibold)
                Text([reader.gradeLevel.clearCodeLabel, reader.centerName].filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

struct ReaderDetailView: View {
    @EnvironmentObject private var appState: AppState
    let reader: ReaderSummary

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                BrandCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(reader.displayName).font(.title2.bold()).foregroundStyle(Brand.ink)
                        if !reader.gradeLevel.isEmpty {
                            Label(reader.gradeLevel.clearCodeLabel, systemImage: "graduationcap.fill")
                        }
                        if !reader.centerName.isEmpty {
                            Label(reader.centerName, systemImage: "building.2.fill")
                        }
                        if !reader.ideaServicesAuthorized {
                            StatusBanner(
                                icon: "exclamationmark.shield",
                                title: "Authorization pending",
                                detail: "Some services remain unavailable until required authorization is recorded."
                            )
                            .padding(.top, 4)
                        }
                    }
                }

                if appState.capabilities?.logSessions == true {
                    NavigationLink {
                        SessionLogView(reader: reader)
                    } label: {
                        Label("Log a session", systemImage: "square.and.pencil")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Brand.deepTeal)
                    .disabled(!reader.ideaServicesAuthorized)
                }

                if appState.capabilities?.viewProgress == true {
                    NavigationLink {
                        ProgressDashboardView(reader: reader)
                    } label: {
                        Label("View progress", systemImage: "chart.xyaxis.line")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .tint(Brand.deepTeal)
                }

                Text("ClearCode checks your current permission and the reader's consent status again whenever you open or save information.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            .padding()
        }
        .background(Brand.linen.opacity(0.55))
        .navigationTitle(reader.firstName)
        .navigationBarTitleDisplayMode(.inline)
    }
}
