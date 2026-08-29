import XCTest
@testable import ClearCode

@MainActor
final class AppStateTests: XCTestCase {
    func testRetryableSessionFailureIsQueued() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let store = OfflineStore(directory: directory, protectedWrites: false)
        let state = AppState(api: RetryableFailureAPI(), offlineStore: store)
        let request = RapidSessionRequest(
            clientRequestId: UUID(), child: 1, accuracyNumerator: 8, accuracyDenominator: 10,
            durationMinutes: 45, scheduledStart: nil, activityCodes: ["guided_practice"],
            errorPatternCodes: [], behavioralObservationCodes: [], behavioralRating: "consistent",
            nextSessionDirection: "Continue.", homePracticeSuggestion: "Practice.", notes: ""
        )

        let result = try await state.submit(request)

        XCTAssertEqual(result, .queued)
        XCTAssertEqual(state.pendingLogs.map(\.id), [request.clientRequestId])
        try? FileManager.default.removeItem(at: directory)
    }
}

private actor RetryableFailureAPI: ClearCodeAPI {
    func hasCredentials() async -> Bool { true }
    func signIn(email: String, password: String) async throws { }
    func bootstrap() async throws -> MobileBootstrap { throw APIClientError.transport("offline") }
    func registerDevice(pushToken: String?) async throws { }
    func logout() async { }
    func sessionDefaults(childID: Int) async throws -> SessionDefaults { throw APIClientError.transport("offline") }
    func todaySessions() async throws -> [InterventionSession] { [] }
    func submitSession(_ request: RapidSessionRequest) async throws -> InterventionSession { throw APIClientError.transport("offline") }
    func progress(childID: Int) async throws -> ProgressDashboard { throw APIClientError.transport("offline") }
    func outcomes() async throws -> [OutcomeSnapshot] { [] }
}
