import Foundation

protocol ClearCodeAPI: Sendable {
    func hasCredentials() async -> Bool
    func signIn(email: String, password: String) async throws
    func bootstrap() async throws -> MobileBootstrap
    func registerDevice(pushToken: String?) async throws
    func logout() async
    func sessionDefaults(childID: Int) async throws -> SessionDefaults
    func todaySessions() async throws -> [InterventionSession]
    func submitSession(_ request: RapidSessionRequest) async throws -> InterventionSession
    func progress(childID: Int) async throws -> ProgressDashboard
    func outcomes() async throws -> [OutcomeSnapshot]
}

enum APIClientError: LocalizedError, Equatable {
    case invalidConfiguration
    case invalidResponse
    case transport(String)
    case unauthorized
    case server(status: Int, message: String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .invalidConfiguration: "The app's server address is not configured."
        case .invalidResponse: "The server returned an unreadable response."
        case .transport: "ClearCode could not reach the server. Check your connection and try again."
        case .unauthorized: "Your session has expired. Please sign in again."
        case .server(_, let message): message
        case .decoding: "The app and server returned incompatible data. Please update the app or contact support."
        }
    }

    var canRetryLater: Bool {
        switch self {
        case .transport: true
        case .server(let status, _): status == 408 || status == 429 || status >= 500
        default: false
        }
    }
}

actor APIClient: ClearCodeAPI {
    private enum Key {
        static let accessToken = "auth.access"
        static let refreshToken = "auth.refresh"
        static let installationID = "device.installation-id"
    }

    private let baseURL: URL
    private let session: URLSession
    private let keychain: KeychainStore
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(baseURL: URL, session: URLSession = .shared, keychain: KeychainStore = KeychainStore()) {
        self.baseURL = baseURL
        self.session = session
        self.keychain = keychain
        encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
    }

    static func configured() -> APIClient {
        guard
            let rawValue = Bundle.main.object(forInfoDictionaryKey: "ClearCodeAPIBaseURL") as? String,
            let url = URL(string: rawValue),
            let scheme = url.scheme,
            scheme == "https" || (scheme == "http" && ["127.0.0.1", "localhost"].contains(url.host))
        else {
            preconditionFailure("ClearCodeAPIBaseURL must be an HTTPS URL or a local Debug URL.")
        }
        return APIClient(baseURL: url)
    }

    func hasCredentials() async -> Bool {
        (try? await keychain.string(for: Key.refreshToken)) != nil
    }

    func signIn(email: String, password: String) async throws {
        let body = SignInRequest(email: email.trimmingCharacters(in: .whitespacesAndNewlines), password: password)
        let pair: TokenPair = try await request(path: "api/v1/auth/token/", method: "POST", body: body, authorized: false)
        try await save(pair)
    }

    func bootstrap() async throws -> MobileBootstrap {
        try await request(path: "api/v1/mobile/bootstrap/", method: "GET")
    }

    func registerDevice(pushToken: String?) async throws {
        let identifier = try await installationID()
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
#if DEBUG
        let environment = "sandbox"
#else
        let environment = "production"
#endif
        let body = MobileDeviceRequest(
            deviceId: identifier,
            pushToken: pushToken ?? "",
            environment: environment,
            appVersion: version
        )
        let _: MobileDevice = try await request(path: "api/v1/mobile/devices/", method: "POST", body: body)
    }

    func logout() async {
        if let identifier = try? await installationID() {
            let body = LogoutRequest(deviceId: identifier)
            let _: EmptyResponse = (try? await request(path: "api/v1/mobile/logout/", method: "POST", body: body)) ?? EmptyResponse()
        }
        try? await keychain.remove(Key.accessToken)
        try? await keychain.remove(Key.refreshToken)
    }

    func sessionDefaults(childID: Int) async throws -> SessionDefaults {
        try await request(path: "api/v1/sessions/defaults/", method: "GET", query: [URLQueryItem(name: "child", value: String(childID))])
    }

    func todaySessions() async throws -> [InterventionSession] {
        try await request(path: "api/v1/sessions/today/", method: "GET")
    }

    func submitSession(_ requestBody: RapidSessionRequest) async throws -> InterventionSession {
        try await request(path: "api/v1/sessions/rapid-log/", method: "POST", body: requestBody)
    }

    func progress(childID: Int) async throws -> ProgressDashboard {
        try await request(path: "api/v1/progress/dashboard/", method: "GET", query: [URLQueryItem(name: "child", value: String(childID))])
    }

    func outcomes() async throws -> [OutcomeSnapshot] {
        let page: PaginatedResponse<OutcomeSnapshot> = try await request(path: "api/v1/outcomes/snapshots/", method: "GET")
        return page.results
    }

    private func request<Response: Decodable>(
        path: String,
        method: String,
        query: [URLQueryItem] = [],
        authorized: Bool = true,
        retryAfterRefresh: Bool = true
    ) async throws -> Response {
        try await request(path: path, method: method, query: query, bodyData: nil, authorized: authorized, retryAfterRefresh: retryAfterRefresh)
    }

    private func request<Body: Encodable, Response: Decodable>(
        path: String,
        method: String,
        query: [URLQueryItem] = [],
        body: Body,
        authorized: Bool = true,
        retryAfterRefresh: Bool = true
    ) async throws -> Response {
        let data = try encoder.encode(body)
        return try await request(path: path, method: method, query: query, bodyData: data, authorized: authorized, retryAfterRefresh: retryAfterRefresh)
    }

    private func request<Response: Decodable>(
        path: String,
        method: String,
        query: [URLQueryItem],
        bodyData: Data?,
        authorized: Bool,
        retryAfterRefresh: Bool
    ) async throws -> Response {
        var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)
        components?.queryItems = query.isEmpty ? nil : query
        guard let url = components?.url else { throw APIClientError.invalidConfiguration }

        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = method
        urlRequest.timeoutInterval = 30
        urlRequest.setValue("application/json", forHTTPHeaderField: "Accept")
        if let bodyData {
            urlRequest.httpBody = bodyData
            urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if authorized {
            guard let access = try await keychain.string(for: Key.accessToken) else {
                throw APIClientError.unauthorized
            }
            urlRequest.setValue("Bearer \(access)", forHTTPHeaderField: "Authorization")
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: urlRequest)
        } catch {
            throw APIClientError.transport(String(describing: error))
        }
        guard let http = response as? HTTPURLResponse else { throw APIClientError.invalidResponse }
        if http.statusCode == 401 && authorized && retryAfterRefresh {
            do {
                try await refresh()
                return try await request(
                    path: path,
                    method: method,
                    query: query,
                    bodyData: bodyData,
                    authorized: true,
                    retryAfterRefresh: false
                )
            } catch {
                try? await keychain.remove(Key.accessToken)
                try? await keychain.remove(Key.refreshToken)
                throw APIClientError.unauthorized
            }
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIClientError.server(status: http.statusCode, message: Self.errorMessage(from: data, status: http.statusCode))
        }
        if Response.self == EmptyResponse.self, data.isEmpty {
            return EmptyResponse() as! Response
        }
        do {
            return try decoder.decode(Response.self, from: data)
        } catch {
            throw APIClientError.decoding(String(describing: error))
        }
    }

    private func refresh() async throws {
        guard let refresh = try await keychain.string(for: Key.refreshToken) else {
            throw APIClientError.unauthorized
        }
        let pair: TokenPair = try await request(
            path: "api/v1/auth/token/refresh/",
            method: "POST",
            body: RefreshRequest(refresh: refresh),
            authorized: false,
            retryAfterRefresh: false
        )
        try await save(pair)
    }

    private func save(_ pair: TokenPair) async throws {
        try await keychain.set(pair.access, for: Key.accessToken)
        try await keychain.set(pair.refresh, for: Key.refreshToken)
    }

    private func installationID() async throws -> UUID {
        if let existing = try await keychain.string(for: Key.installationID), let identifier = UUID(uuidString: existing) {
            return identifier
        }
        let identifier = UUID()
        try await keychain.set(identifier.uuidString, for: Key.installationID)
        return identifier
    }

    private static func errorMessage(from data: Data, status: Int) -> String {
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return "The server could not complete the request (\(status))." }
        if let detail = object["detail"] as? String { return detail }
        let messages = object.sorted(by: { $0.key < $1.key }).compactMap { key, value -> String? in
            if let message = value as? String { return "\(key): \(message)" }
            if let values = value as? [String] { return "\(key): \(values.joined(separator: " "))" }
            return nil
        }
        return messages.isEmpty ? "The server could not complete the request (\(status))." : messages.joined(separator: "\n")
    }
}

private struct SignInRequest: Encodable { let email: String; let password: String }
private struct RefreshRequest: Encodable { let refresh: String }
private struct LogoutRequest: Encodable { let deviceId: UUID }
private struct EmptyResponse: Codable { }
