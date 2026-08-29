import Foundation

struct TokenPair: Codable, Sendable {
    let access: String
    let refresh: String
}

struct MobileBootstrap: Codable, Sendable, Equatable {
    let user: AppUser
    let memberships: [CenterMembership]
    let children: [ReaderSummary]
    let capabilities: AppCapabilities
    let generatedAt: String
}

struct AppUser: Codable, Sendable, Equatable, Identifiable {
    let id: Int
    let email: String
    let firstName: String
    let lastName: String
    let displayName: String
    let role: UserRole
}

enum UserRole: String, Codable, Sendable {
    case superAdmin = "super_admin"
    case schoolAdmin = "school_admin"
    case teacher
    case guardian
    case student
    case unknown

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer().decode(String.self)
        self = UserRole(rawValue: value) ?? .unknown
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

struct CenterMembership: Codable, Sendable, Equatable, Identifiable {
    let id: Int
    let centerId: Int
    let centerName: String
    let centerSlug: String
    let role: String
    let title: String
    let permissions: [String: JSONValue]
}

struct ReaderSummary: Codable, Sendable, Equatable, Hashable, Identifiable {
    let id: Int
    let firstName: String
    let displayName: String
    let gradeLevel: String
    let centerId: Int?
    let centerName: String
    let ideaServicesAuthorized: Bool
}

struct AppCapabilities: Codable, Sendable, Equatable {
    let logSessions: Bool
    let viewProgress: Bool
    let manageSchedules: Bool
    let viewOutcomes: Bool
    let manageConsents: Bool
}

struct MobileDeviceRequest: Codable, Sendable {
    let deviceId: UUID
    let pushToken: String
    let environment: String
    let appVersion: String
}

struct MobileDevice: Codable, Sendable {
    let deviceId: UUID
    let pushToken: String
    let environment: String
    let appVersion: String
    let isActive: Bool
    let lastSeenAt: String
}

struct SessionDefaults: Codable, Sendable, Equatable {
    let child: Int
    let childName: String
    let center: Int
    let methodology: String
    let curriculumName: String
    let curriculumPosition: Int
    let positionCode: String
    let positionTitle: String
    let interventionPart: String
    let interventionPartLabel: String
    let scheduledStart: String
    let suggestedActivities: [CodeOption]
    let behavioralObservationOptions: [CodeOption]
    let behavioralRatingOptions: [CodeOption]
    let errorPatternOptions: [CodeOption]
    let nextSessionDirection: String
    let homePracticeSuggestion: String
}

struct CodeOption: Codable, Sendable, Equatable, Identifiable {
    let code: String
    let label: String

    var id: String { code }
}

struct RapidSessionRequest: Codable, Sendable, Equatable, Identifiable {
    let clientRequestId: UUID
    let child: Int
    let accuracyNumerator: Int
    let accuracyDenominator: Int
    let durationMinutes: Int
    let scheduledStart: String?
    let activityCodes: [String]
    let errorPatternCodes: [String]
    let behavioralObservationCodes: [String]
    let behavioralRating: String
    let nextSessionDirection: String
    let homePracticeSuggestion: String
    let notes: String

    var id: UUID { clientRequestId }
}

struct InterventionSession: Codable, Sendable, Equatable, Identifiable {
    let id: Int
    let clientRequestId: UUID?
    let child: Int
    let childName: String
    let specialistName: String
    let positionCode: String
    let status: String
    let interventionPart: String
    let scheduledStart: String
    let startedAt: String?
    let endedAt: String?
    let accuracyRate: FlexibleDouble?
    let accuracyNumerator: Int?
    let accuracyDenominator: Int?
    let nextSessionDirection: String
    let homePracticeSuggestion: String
    let notes: String
}

struct ProgressDashboard: Codable, Sendable, Equatable {
    let child: ProgressChild
    let generatedAt: String
    let weekStart: String
    let summary: ProgressSummary
    let skills: [SkillProgress]
    let recentMastery: [RecentMastery]
    let fluencyTrend: [FluencyPoint]
    let decodableTextProgress: [DecodableResult]
    let progressOverTime: [ProgressPoint]
    let specialistNote: String
    let specialistName: String
    let homePractice: String
    let milestone: Milestone
    let foundationalSkillsOnly: Bool
}

struct ProgressChild: Codable, Sendable, Equatable, Identifiable {
    let id: Int
    let firstName: String
    let displayName: String
}

struct ProgressSummary: Codable, Sendable, Equatable {
    let trackedSkills: Int
    let masteredSkills: Int
    let completedSessions: Int
    let latestAccuracy: FlexibleDouble?
}

struct SkillProgress: Codable, Sendable, Equatable, Identifiable {
    let id: Int
    let code: String
    let name: String
    let domain: String
    let status: String
    let score: FlexibleDouble?
    let updatedAt: String
}

struct RecentMastery: Codable, Sendable, Equatable, Identifiable {
    let skill: String
    let code: String
    let masteredAt: String
    let score: FlexibleDouble?

    var id: String { "\(code)-\(masteredAt)" }
}

struct FluencyPoint: Codable, Sendable, Equatable, Identifiable {
    let sessionId: Int
    let date: String
    let wcpm: FlexibleDouble

    var id: Int { sessionId }
}

struct DecodableResult: Codable, Sendable, Equatable, Identifiable {
    let sessionId: Int
    let date: String
    let title: String
    let accuracy: FlexibleDouble?
    let completed: Bool

    var id: Int { sessionId }
}

struct ProgressPoint: Codable, Sendable, Equatable, Identifiable {
    let sessionId: Int
    let date: String
    let accuracy: FlexibleDouble?
    let wcpm: FlexibleDouble?

    var id: Int { sessionId }
}

struct Milestone: Codable, Sendable, Equatable {
    let status: String
    let label: String
    let currentPosition: String?
    let positionsRemaining: Int?
    let estimatedWeeks: Int?
    let estimatedDate: String?
    let disclaimer: String?
}

struct PaginatedResponse<Value: Codable & Sendable>: Codable, Sendable {
    let count: Int
    let next: String?
    let previous: String?
    let results: [Value]
}

struct OutcomeSnapshot: Codable, Sendable, Equatable, Identifiable {
    let id: Int
    let centerKey: String
    let methodology: String
    let gradeBand: String
    let windowType: String
    let windowStart: String
    let windowEnd: String
    let metricScope: String
    let aggregateVersion: String
    let privacyFloor: Int
    let metrics: [String: JSONValue]
    let sourceCounts: [String: JSONValue]
    let generatedAt: String
}

struct FlexibleDouble: Codable, Sendable, Equatable, Hashable {
    let value: Double

    init(_ value: Double) {
        self.value = value
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let number = try? container.decode(Double.self) {
            value = number
            return
        }
        let string = try container.decode(String.self)
        guard let number = Double(string) else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Expected a number or numeric string.")
        }
        value = number
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(value)
    }
}

enum JSONValue: Codable, Sendable, Equatable, Hashable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([String: JSONValue].self) { self = .object(value) }
        else { self = .array(try container.decode([JSONValue].self)) }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    var displayText: String {
        switch self {
        case .string(let value): value
        case .number(let value): value.formatted(.number.precision(.fractionLength(0...2)))
        case .bool(let value): value ? "Yes" : "No"
        case .null: "—"
        case .array, .object: "Details"
        }
    }
}
