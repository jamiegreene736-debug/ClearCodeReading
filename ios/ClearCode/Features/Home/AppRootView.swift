import SwiftUI

struct AppRootView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        Group {
            switch appState.phase {
            case .starting:
                ZStack {
                    Brand.linen.ignoresSafeArea()
                    ProgressView("Opening ClearCode…").tint(Brand.deepTeal)
                }
            case .signedOut:
                LoginView()
            case .signedIn:
                RootTabView()
            }
        }
    }
}

struct RootTabView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        TabView {
            NavigationStack { HomeView() }
                .tabItem { Label("Home", systemImage: "house.fill") }

            NavigationStack { ReadersView() }
                .tabItem { Label("Readers", systemImage: "person.2.fill") }

            if appState.capabilities?.logSessions == true {
                NavigationStack { TodaySessionsView() }
                    .tabItem { Label("Sessions", systemImage: "checklist") }
            }

            if appState.capabilities?.viewOutcomes == true {
                NavigationStack { OutcomesView() }
                    .tabItem { Label("Outcomes", systemImage: "chart.line.uptrend.xyaxis") }
            }

            NavigationStack { SettingsView() }
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
        }
        .tint(Brand.deepTeal)
    }
}
