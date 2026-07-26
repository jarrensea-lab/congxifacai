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
    static let writeEnvironmentKey = "CONGXI_YITAOJIN_WRITE_ENABLED"

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

    func assertLoggedIn(_ probe: ProbeData) throws {
        guard probe.appRunning else {
            throw BridgeFailure(
                "app_not_running",
                "GF-Trader.app is not running"
            )
        }
        guard probe.applicationPathValid else {
            throw BridgeFailure(
                "application_path_mismatch",
                "The running application is not /Applications/GF-Trader.app"
            )
        }
        guard probe.accessibilityTrusted else {
            throw BridgeFailure(
                "accessibility_permission_missing",
                "macOS Accessibility permission is required"
            )
        }
        guard probe.loginState == "logged_in" else {
            throw BridgeFailure(
                probe.loginState == "not_logged_in"
                    ? "not_logged_in"
                    : "login_state_unknown",
                "The broker login state is not safely confirmed"
            )
        }
    }

    func assertUniqueWriteTarget(count: Int) throws {
        guard count == 1 else {
            throw BridgeFailure(
                count == 0
                    ? "safe_watchlist_action_unavailable"
                    : "ambiguous_watchlist_target",
                "A watchlist write requires exactly one safe exact-code target"
            )
        }
    }

    func assertWriteAllowed(
        command: BridgeCommand,
        code: String,
        targetPath: [AXNodeSummary],
        environment: [String: String]
    ) throws {
        try assertWriteEnabled(
            command: command,
            code: code,
            environment: environment
        )
        try assertReadable(path: targetPath)

        let combined = targetPath.map(\.text).joined(separator: " ")
        guard combined.contains("自选") else {
            throw BridgeFailure(
                "unsafe_ui_target",
                "Watchlist writes must stay inside the self-selected list"
            )
        }
        guard combined.contains(code) else {
            throw BridgeFailure(
                "unsafe_ui_target",
                "The safe UI target does not identify the requested stock code"
            )
        }
        let actionLabels = command == .addWatchlist
            ? ["添加自选", "加自选"]
            : ["删除自选", "移除自选", "取消自选"]
        guard actionLabels.contains(where: combined.contains) else {
            throw BridgeFailure(
                "unsafe_ui_target",
                "The requested UI action is outside the watchlist mutation allowlist"
            )
        }
    }

    func assertWriteEnabled(
        command: BridgeCommand,
        code: String,
        environment: [String: String]
    ) throws {
        guard environment[Self.writeEnvironmentKey]?.lowercased() == "true" else {
            throw BridgeFailure(
                "write_disabled",
                "Watchlist writes require an explicit bridge write-enable gate"
            )
        }
        guard command == .addWatchlist || command == .removeWatchlist else {
            throw BridgeFailure(
                "unsafe_command",
                "Only watchlist add and remove commands may write to the UI"
            )
        }
        let codePattern = try! NSRegularExpression(pattern: #"^\d{6}$"#)
        let range = NSRange(code.startIndex ..< code.endIndex, in: code)
        guard codePattern.firstMatch(in: code, range: range) != nil else {
            throw BridgeFailure(
                "invalid_stock_code",
                "Watchlist writes require one canonical 6-digit code"
            )
        }
    }

    func isForbidden(_ node: AXNodeSummary) -> Bool {
        Self.forbiddenLabels.contains(where: node.text.contains)
    }
}
