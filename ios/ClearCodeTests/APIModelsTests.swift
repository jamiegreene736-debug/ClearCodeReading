import XCTest
@testable import ClearCode

final class APIModelsTests: XCTestCase {
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    func testBootstrapDecodesServerContract() throws {
        let data = Data(
            #"""
            {
              "user": {"id": 7, "email": "teacher@example.com", "first_name": "Taylor", "last_name": "Reed", "display_name": "Taylor Reed", "role": "teacher"},
              "memberships": [{"id": 2, "center_id": 4, "center_name": "ClearCode Center", "center_slug": "clearcode", "role": "specialist", "title": "Reading Specialist", "permissions": {}}],
              "children": [{"id": 9, "first_name": "Avery", "display_name": "Avery Reader", "grade_level": "grade_1", "center_id": 4, "center_name": "ClearCode Center", "idea_services_authorized": true}],
              "capabilities": {"log_sessions": true, "view_progress": true, "manage_schedules": false, "view_outcomes": false, "manage_consents": false},
              "generated_at": "2026-08-29T15:00:00Z"
            }
            """#.utf8
        )

        let bootstrap = try decoder.decode(MobileBootstrap.self, from: data)

        XCTAssertEqual(bootstrap.user.displayName, "Taylor Reed")
        XCTAssertEqual(bootstrap.children.first?.displayName, "Avery Reader")
        XCTAssertTrue(bootstrap.capabilities.logSessions)
        XCTAssertFalse(bootstrap.capabilities.viewOutcomes)
    }

    func testFlexibleDoubleAcceptsNumberAndDecimalString() throws {
        XCTAssertEqual(try decoder.decode(FlexibleDouble.self, from: Data("91.25".utf8)).value, 91.25)
        XCTAssertEqual(try decoder.decode(FlexibleDouble.self, from: Data(#""88.50""#.utf8)).value, 88.5)
    }

    func testOutcomeSnapshotDecodesDeidentifiedContract() throws {
        let data = Data(
            #"""
            {
              "id": 12,
              "center_key": "center-4f2c",
              "methodology": "structured_literacy",
              "grade_band": "grades_1_2",
              "window_type": "monthly",
              "window_start": "2026-08-01",
              "window_end": "2026-08-31",
              "metric_scope": "center",
              "aggregate_version": "v1",
              "privacy_floor": 5,
              "metrics": {
                "cohort_students": 8,
                "weighted_accuracy_rate": 84.25
              },
              "source_counts": {"skill_observations": 26},
              "generated_at": "2026-08-29T15:00:00Z"
            }
            """#.utf8
        )

        let snapshot = try decoder.decode(OutcomeSnapshot.self, from: data)

        XCTAssertEqual(snapshot.centerKey, "center-4f2c")
        XCTAssertEqual(snapshot.privacyFloor, 5)
        XCTAssertEqual(snapshot.metrics["cohort_students"], .number(8))
        XCTAssertEqual(snapshot.metrics["weighted_accuracy_rate"]?.displayText, "84.25")
    }

    func testUnknownServerRoleFailsClosed() throws {
        let role = try decoder.decode(UserRole.self, from: Data(#""new_role""#.utf8))
        XCTAssertEqual(role, .unknown)
    }
}
