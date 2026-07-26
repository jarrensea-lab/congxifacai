import AppKit
import ApplicationServices
import CryptoKit
import Foundation

final class AXClient {
    static let bundleIdentifier = "cn.com.gf.trader"
    static let expectedApplicationPath = "/Applications/GF-Trader.app"

    private let safetyPolicy = SafetyPolicy()

    var accessibilityTrusted: Bool {
        AXIsProcessTrusted()
    }

    func runningApplication() -> NSRunningApplication? {
        NSRunningApplication.runningApplications(
            withBundleIdentifier: Self.bundleIdentifier
        ).first
    }

    func applicationPathValid(_ app: NSRunningApplication?) -> Bool {
        guard let path = app?.bundleURL?.standardizedFileURL.path else {
            return false
        }
        return path == Self.expectedApplicationPath
    }

    func probe() -> ProbeData {
        let app = runningApplication()
        guard app != nil else {
            return ProbeData(
                appRunning: false,
                applicationPathValid: false,
                accessibilityTrusted: accessibilityTrusted,
                loginState: "unknown",
                interfaceSignature: nil
            )
        }
        let pathValid = applicationPathValid(app)
        guard accessibilityTrusted, pathValid else {
            return ProbeData(
                appRunning: true,
                applicationPathValid: pathValid,
                accessibilityTrusted: accessibilityTrusted,
                loginState: "unknown",
                interfaceSignature: nil
            )
        }
        do {
            let snapshot = try applicationSnapshot()
            return ProbeData(
                appRunning: true,
                applicationPathValid: true,
                accessibilityTrusted: true,
                loginState: loginState(from: snapshot),
                interfaceSignature: interfaceSignature(snapshot)
            )
        } catch {
            return ProbeData(
                appRunning: true,
                applicationPathValid: true,
                accessibilityTrusted: true,
                loginState: "unknown",
                interfaceSignature: nil
            )
        }
    }

    func snapshotForPage(labels: [String]) throws -> AXSnapshotNode {
        let root = try applicationElement()
        if let navigation = findElement(
            in: root,
            matchingAny: labels,
            maxDepth: 8,
            maxNodes: 1_500
        ) {
            let summary = summary(of: navigation)
            try safetyPolicy.assertReadable(path: [summary])
            try press(navigation)
            Thread.sleep(forTimeInterval: 0.35)
        }
        return try snapshot(
            root,
            path: [],
            depth: 0,
            nodeBudget: NodeBudget(remaining: 2_500)
        )
    }

    func applicationSnapshot() throws -> AXSnapshotNode {
        try snapshot(
            applicationElement(),
            path: [],
            depth: 0,
            nodeBudget: NodeBudget(remaining: 2_500)
        )
    }

    private func applicationElement() throws -> AXUIElement {
        guard accessibilityTrusted else {
            throw BridgeFailure(
                "accessibility_permission_missing",
                "macOS Accessibility permission is required"
            )
        }
        guard let app = runningApplication() else {
            throw BridgeFailure("app_not_running", "GF-Trader.app is not running")
        }
        guard applicationPathValid(app) else {
            throw BridgeFailure(
                "application_path_mismatch",
                "The running application is not /Applications/GF-Trader.app"
            )
        }
        return AXUIElementCreateApplication(app.processIdentifier)
    }

    private func loginState(from snapshot: AXSnapshotNode) -> String {
        let text = snapshot.combinedText
        if text.contains("总资产") || text.contains("可用资金") || text.contains("我的持仓") {
            return "logged_in"
        }
        if text.contains("登录") || text.contains("账号登录") {
            return "not_logged_in"
        }
        return "unknown"
    }

    private func interfaceSignature(_ snapshot: AXSnapshotNode) -> String {
        let structuralText = snapshot.flattened.prefix(400).map { node in
            [
                node.summary.role,
                node.summary.title ?? "",
                node.summary.label ?? "",
            ].joined(separator: "|")
        }.joined(separator: "\n")
        return SHA256.hash(data: Data(structuralText.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
    }

    private func findElement(
        in root: AXUIElement,
        matchingAny labels: [String],
        maxDepth: Int,
        maxNodes: Int
    ) -> AXUIElement? {
        var remaining = maxNodes

        func visit(_ element: AXUIElement, depth: Int) -> AXUIElement? {
            guard depth <= maxDepth, remaining > 0 else {
                return nil
            }
            remaining -= 1
            let node = summary(of: element)
            guard !safetyPolicy.isForbidden(node) else {
                return nil
            }
            if labels.contains(where: node.text.contains) {
                return element
            }
            for child in children(of: element) {
                if let match = visit(child, depth: depth + 1) {
                    return match
                }
            }
            return nil
        }

        return visit(root, depth: 0)
    }

    private func press(_ element: AXUIElement) throws {
        var rawActions: CFArray?
        guard
            AXUIElementCopyActionNames(element, &rawActions) == .success,
            let actions = rawActions as? [String],
            actions.contains(kAXPressAction as String)
        else {
            throw BridgeFailure(
                "ui_action_unavailable",
                "The safe navigation target is not pressable"
            )
        }
        guard AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else {
            throw BridgeFailure(
                "ui_action_failed",
                "The safe navigation action failed"
            )
        }
    }

    private func snapshot(
        _ element: AXUIElement,
        path: [AXNodeSummary],
        depth: Int,
        nodeBudget: NodeBudget
    ) throws -> AXSnapshotNode {
        guard depth <= 14 else {
            return AXSnapshotNode(summary: summary(of: element), children: [])
        }
        guard nodeBudget.take() else {
            throw BridgeFailure(
                "accessibility_tree_too_large",
                "The Accessibility tree exceeded the safe node budget"
            )
        }
        let node = summary(of: element)
        let currentPath = path + [node]
        try safetyPolicy.assertReadable(path: currentPath)
        let childSnapshots = try children(of: element).compactMap { child -> AXSnapshotNode? in
            let childSummary = summary(of: child)
            if safetyPolicy.isForbidden(childSummary) {
                return nil
            }
            return try snapshot(
                child,
                path: currentPath,
                depth: depth + 1,
                nodeBudget: nodeBudget
            )
        }
        return AXSnapshotNode(summary: node, children: childSnapshots)
    }

    private func summary(of element: AXUIElement) -> AXNodeSummary {
        AXNodeSummary(
            role: stringAttribute(element, kAXRoleAttribute) ?? "AXUnknown",
            title: stringAttribute(element, kAXTitleAttribute),
            label: stringAttribute(element, kAXDescriptionAttribute),
            value: stringAttribute(element, kAXValueAttribute)
        )
    }

    private func children(of element: AXUIElement) -> [AXUIElement] {
        var rawValue: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(
                element,
                kAXChildrenAttribute as CFString,
                &rawValue
            ) == .success,
            let children = rawValue as? [AXUIElement]
        else {
            return []
        }
        return children
    }

    private func stringAttribute(
        _ element: AXUIElement,
        _ attribute: String
    ) -> String? {
        var rawValue: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(
                element,
                attribute as CFString,
                &rawValue
            ) == .success,
            let value = rawValue
        else {
            return nil
        }
        if let string = value as? String {
            return string.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if let number = value as? NSNumber {
            return number.stringValue
        }
        return nil
    }
}

private final class NodeBudget {
    private(set) var remaining: Int

    init(remaining: Int) {
        self.remaining = remaining
    }

    func take() -> Bool {
        guard remaining > 0 else {
            return false
        }
        remaining -= 1
        return true
    }
}
