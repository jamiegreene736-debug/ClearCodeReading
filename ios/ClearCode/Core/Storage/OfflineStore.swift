import Foundation

struct PendingSessionLog: Codable, Sendable, Equatable, Identifiable {
    let request: RapidSessionRequest
    var failureMessage: String?
    let queuedAt: Date

    var id: UUID { request.clientRequestId }
    var needsReview: Bool { failureMessage != nil }
}

actor OfflineStore {
    private let directory: URL
    private let protectedWrites: Bool
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    init(directory: URL? = nil, protectedWrites: Bool = true) {
        if let directory {
            self.directory = directory
        } else {
            let root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            self.directory = root.appendingPathComponent("ClearCode", isDirectory: true)
        }
        self.protectedWrites = protectedWrites
        encoder.dateEncodingStrategy = .iso8601
        decoder.dateDecodingStrategy = .iso8601
    }

    func cachedBootstrap() throws -> MobileBootstrap? {
        try read(MobileBootstrap.self, from: "bootstrap.json")
    }

    func cache(_ bootstrap: MobileBootstrap) throws {
        try write(bootstrap, to: "bootstrap.json")
    }

    func pendingLogs() throws -> [PendingSessionLog] {
        try read([PendingSessionLog].self, from: "pending-sessions.json") ?? []
    }

    func enqueue(_ request: RapidSessionRequest) throws {
        var items = try pendingLogs()
        guard !items.contains(where: { $0.id == request.clientRequestId }) else { return }
        items.append(PendingSessionLog(request: request, failureMessage: nil, queuedAt: Date()))
        try write(items, to: "pending-sessions.json")
    }

    func replacePendingLogs(_ items: [PendingSessionLog]) throws {
        try write(items, to: "pending-sessions.json")
    }

    func clearUserData() throws {
        for name in ["bootstrap.json", "pending-sessions.json"] {
            let url = directory.appendingPathComponent(name)
            if FileManager.default.fileExists(atPath: url.path) {
                try FileManager.default.removeItem(at: url)
            }
        }
    }

    private func read<Value: Decodable>(_ type: Value.Type, from name: String) throws -> Value? {
        let url = directory.appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return try decoder.decode(type, from: Data(contentsOf: url))
    }

    private func write<Value: Encodable>(_ value: Value, to name: String) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let options: Data.WritingOptions = protectedWrites ? [.atomic, .completeFileProtection] : [.atomic]
        try encoder.encode(value).write(to: directory.appendingPathComponent(name), options: options)
    }
}
