import XCTest
@testable import ClearCode

final class OfflineStoreTests: XCTestCase {
    func testQueueIsIdempotentAndPersistsReviewState() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let store = OfflineStore(directory: directory, protectedWrites: false)
        let request = RapidSessionRequest(
            clientRequestId: UUID(),
            child: 12,
            accuracyNumerator: 9,
            accuracyDenominator: 10,
            durationMinutes: 45,
            scheduledStart: nil,
            activityCodes: ["word_reading"],
            errorPatternCodes: [],
            behavioralObservationCodes: [],
            behavioralRating: "consistent",
            nextSessionDirection: "Continue.",
            homePracticeSuggestion: "Practice.",
            notes: ""
        )

        try await store.enqueue(request)
        try await store.enqueue(request)
        var values = try await store.pendingLogs()
        XCTAssertEqual(values.count, 1)

        values[0].failureMessage = "Placement changed."
        try await store.replacePendingLogs(values)
        let reloaded = try await store.pendingLogs()
        XCTAssertEqual(reloaded.first?.failureMessage, "Placement changed.")

        try? FileManager.default.removeItem(at: directory)
    }
}
