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

    func testRefreshNotificationAuthorizationShowsExistingPermission() async {
        let authorizer = NotificationAuthorizerStub(state: .authorized)
        let state = AppState(
            api: RetryableFailureAPI(),
            offlineStore: OfflineStore(protectedWrites: false),
            notificationAuthorizer: authorizer
        )

        await state.refreshNotificationAuthorization()

        XCTAssertEqual(state.notificationAuthorizationState, .authorized)
    }

    func testGrantedNotificationRequestUpdatesStateAndRegistersDevice() async {
        let authorizer = NotificationAuthorizerStub(state: .notDetermined, requestedState: .authorized)
        let registration = RemoteNotificationRegistrationSpy()
        let state = AppState(
            api: RetryableFailureAPI(),
            offlineStore: OfflineStore(protectedWrites: false),
            notificationAuthorizer: authorizer,
            registerForRemoteNotifications: { registration.register() }
        )

        await state.requestPushNotifications()
        let requestCount = await authorizer.requestCount

        XCTAssertEqual(state.notificationAuthorizationState, .authorized)
        XCTAssertEqual(registration.callCount, 1)
        XCTAssertEqual(requestCount, 1)
    }

    func testDeniedNotificationPermissionDoesNotPromptAgain() async {
        let authorizer = NotificationAuthorizerStub(state: .denied)
        let registration = RemoteNotificationRegistrationSpy()
        let state = AppState(
            api: RetryableFailureAPI(),
            offlineStore: OfflineStore(protectedWrites: false),
            notificationAuthorizer: authorizer,
            registerForRemoteNotifications: { registration.register() }
        )

        await state.requestPushNotifications()
        let requestCount = await authorizer.requestCount

        XCTAssertEqual(state.notificationAuthorizationState, .denied)
        XCTAssertEqual(registration.callCount, 0)
        XCTAssertEqual(requestCount, 0)
        XCTAssertEqual(state.alertMessage, "Notifications remain off. You can enable them later in iOS Settings.")
    }
}

private actor NotificationAuthorizerStub: NotificationAuthorizing {
    private var state: NotificationAuthorizationState
    private let requestedState: NotificationAuthorizationState
    private(set) var requestCount = 0

    init(
        state: NotificationAuthorizationState,
        requestedState: NotificationAuthorizationState = .denied
    ) {
        self.state = state
        self.requestedState = requestedState
    }

    func authorizationState() -> NotificationAuthorizationState {
        state
    }

    func requestAuthorization() -> Bool {
        requestCount += 1
        state = requestedState
        return requestedState.isEnabled
    }
}

@MainActor
private final class RemoteNotificationRegistrationSpy {
    private(set) var callCount = 0

    func register() {
        callCount += 1
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
