import Foundation

struct AXNodeSummary: Codable, Equatable, Sendable {
    let role: String
    let title: String?
    let label: String?
    let value: String?

    var text: String {
        [title, label, value]
            .compactMap { $0 }
            .joined(separator: " ")
    }
}

struct AXSnapshotNode: Codable, Equatable, Sendable {
    let summary: AXNodeSummary
    let children: [AXSnapshotNode]

    var flattened: [AXSnapshotNode] {
        [self] + children.flatMap(\.flattened)
    }

    func value(forLabel label: String) -> String? {
        flattened.first { node in
            node.summary.title == label || node.summary.label == label
        }?.summary.value
    }

    var combinedText: String {
        flattened.map(\.summary.text).joined(separator: " ")
    }
}

struct SafetyPolicy: Sendable {
    static let forbiddenLabels = [
        "委托",
        "买入",
        "卖出",
        "撤单",
        "清仓",
        "转账",
        "银证",
        "密码",
        "验证码",
    ]

    func assertReadable(path: [AXNodeSummary]) throws {
        for node in path {
            if Self.forbiddenLabels.contains(where: node.text.contains) {
                throw BridgeFailure(
                    "unsafe_ui_target",
                    "The requested UI path crosses a forbidden trading or credential area"
                )
            }
        }
    }

    func isForbidden(_ node: AXNodeSummary) -> Bool {
        Self.forbiddenLabels.contains(where: node.text.contains)
    }
}
