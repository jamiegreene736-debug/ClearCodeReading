import SwiftUI

enum Brand {
    static let ink = Color(red: 15 / 255, green: 43 / 255, blue: 53 / 255)
    static let forest = Color(red: 44 / 255, green: 74 / 255, blue: 69 / 255)
    static let deepTeal = Color(red: 26 / 255, green: 122 / 255, blue: 122 / 255)
    static let mediumTeal = Color(red: 46 / 255, green: 184 / 255, blue: 184 / 255)
    static let eucalyptus = Color(red: 90 / 255, green: 158 / 255, blue: 143 / 255)
    static let seafoam = Color(red: 168 / 255, green: 207 / 255, blue: 196 / 255)
    static let linen = Color(red: 247 / 255, green: 242 / 255, blue: 234 / 255)
    static let gold = Color(red: 245 / 255, green: 166 / 255, blue: 35 / 255)
}

struct BrandCard<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 18))
    }
}

struct StatusBanner: View {
    let icon: String
    let title: String
    let detail: String
    var color: Color = Brand.gold

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(color)
                .font(.title3)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.headline)
                Text(detail).font(.subheadline).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .combine)
    }
}

extension String {
    var clearCodeLabel: String {
        replacingOccurrences(of: "_", with: " ").capitalized
    }

    var clearCodeDate: String {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let standard = ISO8601DateFormatter()
        standard.formatOptions = [.withInternetDateTime]
        let date = fractional.date(from: self) ?? standard.date(from: self)
        return date?.formatted(date: .abbreviated, time: .shortened) ?? self
    }
}
