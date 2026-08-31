import Foundation
import UIKit
import UserNotifications

enum NotificationAuthorizationState: Equatable, Sendable {
    case notDetermined
    case denied
    case authorized
    case provisional
    case ephemeral
    case unknown

    var isEnabled: Bool {
        switch self {
        case .authorized, .provisional, .ephemeral:
            true
        case .notDetermined, .denied, .unknown:
            false
        }
    }
}

protocol NotificationAuthorizing: Sendable {
    func authorizationState() async -> NotificationAuthorizationState
    func requestAuthorization() async throws -> Bool
}

struct SystemNotificationAuthorizer: NotificationAuthorizing {
    func authorizationState() async -> NotificationAuthorizationState {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        switch settings.authorizationStatus {
        case .notDetermined:
            return .notDetermined
        case .denied:
            return .denied
        case .authorized:
            return .authorized
        case .provisional:
            return .provisional
        case .ephemeral:
            return .ephemeral
        @unknown default:
            return .unknown
        }
    }

    func requestAuthorization() async throws -> Bool {
        try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])
    }
}

@MainActor
final class AppState: ObservableObject {
    enum Phase: Equatable {
        case starting
        case signedOut
        case signedIn
    }

    enum SubmissionResult: Equatable {
        case saved(InterventionSession)
        case queued
    }

    @Published private(set) var phase: Phase = .starting
    @Published private(set) var bootstrap: MobileBootstrap?
    @Published private(set) var isOffline = false
    @Published private(set) var pendingLogs: [PendingSessionLog] = []
    @Published private(set) var notificationAuthorizationState: NotificationAuthorizationState = .unknown
    @Published var alertMessage: String?
    @Published var isBusy = false

    let api: any ClearCodeAPI
    private let offlineStore: OfflineStore
    private let notificationAuthorizer: any NotificationAuthorizing
    private let registerForRemoteNotifications: @MainActor @Sendable () -> Void
    private var pushToken: String?

    init(
        api: any ClearCodeAPI = APIClient.configured(),
        offlineStore: OfflineStore = OfflineStore(),
        notificationAuthorizer: any NotificationAuthorizing = SystemNotificationAuthorizer(),
        registerForRemoteNotifications: @escaping @MainActor @Sendable () -> Void = {
            UIApplication.shared.registerForRemoteNotifications()
        }
    ) {
        self.api = api
        self.offlineStore = offlineStore
        self.notificationAuthorizer = notificationAuthorizer
        self.registerForRemoteNotifications = registerForRemoteNotifications
    }

    var capabilities: AppCapabilities? { bootstrap?.capabilities }

    func start() async {
        guard phase == .starting else { return }
        await refreshNotificationAuthorization()
        guard await api.hasCredentials() else {
            phase = .signedOut
            return
        }
        await loadAuthenticatedSession()
    }

    func signIn(email: String, password: String) async {
        guard !email.isEmpty, !password.isEmpty else {
            alertMessage = "Enter your email address and password."
            return
        }
        isBusy = true
        defer { isBusy = false }
        do {
            try await api.signIn(email: email, password: password)
            await loadAuthenticatedSession()
        } catch {
            phase = .signedOut
            alertMessage = error.localizedDescription
        }
    }

    func signOut() async {
        isBusy = true
        await api.logout()
        try? await offlineStore.clearUserData()
        bootstrap = nil
        pendingLogs = []
        isOffline = false
        phase = .signedOut
        isBusy = false
    }

    func refresh() async {
        await refreshNotificationAuthorization()
        guard phase == .signedIn else { return }
        await loadAuthenticatedSession()
    }

    func submit(_ request: RapidSessionRequest) async throws -> SubmissionResult {
        do {
            let session = try await api.submitSession(request)
            await reloadPendingLogs()
            return .saved(session)
        } catch let error as APIClientError where error.canRetryLater {
            try await offlineStore.enqueue(request)
            await reloadPendingLogs()
            isOffline = true
            return .queued
        }
    }

    func flushPendingLogs(includeNeedsReview: Bool = false) async {
        let items = (try? await offlineStore.pendingLogs()) ?? []
        guard !items.isEmpty else {
            pendingLogs = []
            return
        }
        var remaining: [PendingSessionLog] = []
        for var item in items {
            if item.needsReview && !includeNeedsReview {
                remaining.append(item)
                continue
            }
            do {
                _ = try await api.submitSession(item.request)
            } catch let error as APIClientError where error.canRetryLater {
                item.failureMessage = nil
                remaining.append(item)
            } catch {
                item.failureMessage = error.localizedDescription
                remaining.append(item)
            }
        }
        try? await offlineStore.replacePendingLogs(remaining)
        pendingLogs = remaining
    }

    func requestPushNotifications() async {
        do {
            await refreshNotificationAuthorization()

            if notificationAuthorizationState.isEnabled {
                registerForRemoteNotifications()
                return
            }

            guard notificationAuthorizationState != .denied else {
                alertMessage = "Notifications remain off. You can enable them later in iOS Settings."
                return
            }

            let granted = try await notificationAuthorizer.requestAuthorization()
            await refreshNotificationAuthorization()

            if granted && !notificationAuthorizationState.isEnabled {
                notificationAuthorizationState = .authorized
            }

            guard notificationAuthorizationState.isEnabled else {
                alertMessage = "Notifications remain off. You can enable them later in iOS Settings."
                return
            }

            registerForRemoteNotifications()
        } catch {
            alertMessage = "Notifications could not be enabled: \(error.localizedDescription)"
        }
    }

    func refreshNotificationAuthorization() async {
        notificationAuthorizationState = await notificationAuthorizer.authorizationState()
    }

    func receivedPushToken(_ token: String) async {
        pushToken = token
        guard phase == .signedIn else { return }
        try? await api.registerDevice(pushToken: token)
    }

    private func loadAuthenticatedSession() async {
        do {
            let value = try await api.bootstrap()
            bootstrap = value
            try? await offlineStore.cache(value)
            try? await api.registerDevice(pushToken: pushToken)
            phase = .signedIn
            isOffline = false
            await flushPendingLogs()
        } catch APIClientError.unauthorized {
            await api.logout()
            bootstrap = nil
            phase = .signedOut
            alertMessage = APIClientError.unauthorized.localizedDescription
        } catch {
            if let cached = try? await offlineStore.cachedBootstrap() {
                bootstrap = cached
                phase = .signedIn
                isOffline = true
                alertMessage = "Showing saved information while ClearCode reconnects."
                await reloadPendingLogs()
            } else {
                phase = .signedOut
                alertMessage = error.localizedDescription
            }
        }
    }

    private func reloadPendingLogs() async {
        pendingLogs = (try? await offlineStore.pendingLogs()) ?? []
    }
}

extension Notification.Name {
    static let clearCodePushToken = Notification.Name("clearCodePushToken")
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        NotificationCenter.default.post(name: .clearCodePushToken, object: token)
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        NotificationCenter.default.post(name: .clearCodePushToken, object: nil)
    }
}
