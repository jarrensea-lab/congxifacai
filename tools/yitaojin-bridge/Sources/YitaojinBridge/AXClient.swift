import AppKit
import ApplicationServices
import CryptoKit
import Foundation

final class AXClient {
    static let bundleIdentifier = "cn.com.gf.trader"
    static let expectedApplicationPath = "/Applications/GF-Trader.app"
    static let snapshotMaxDepth = 24
    static let snapshotNodeBudget = 7_500
    static let rowChildLimit = 16
    static let accountAssetNavigationAttempts = 2
    static let accountHoldingsNavigationAttempts = 2
    static let accountHoldingsPollCount = 5
    static let accountAssetNavigationStages = [
        ["我的"],
        ["资产全景"],
    ]
    static let accountHoldingsNavigationStages = [
        ["自选股", "自选"],
        ["我的持仓"],
    ]
    static let safeLoginIndicators = ["锁定账号"]
    static let safeHoldingsLoginLabels = [
        "普通持仓",
        "可用数量",
        "成本价",
    ]

    static func childTraversalAttributes(forRole role: String) -> [String] {
        if role == "AXTable" {
            return [
                kAXVisibleRowsAttribute as String,
                kAXRowsAttribute as String,
                kAXChildrenAttribute as String,
            ]
        }
        return [kAXChildrenAttribute as String]
    }

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
            let root = try applicationElement()
            let trustedLogin = findElement(
                in: root,
                maxDepth: 10,
                maxNodes: 1_000
            ) { node in
                Self.safeLoginIndicators.contains(where: node.text.contains)
            } != nil
            let snapshot = try snapshot(
                root,
                path: [],
                depth: 0,
                nodeBudget: NodeBudget(remaining: Self.snapshotNodeBudget)
            )
            var detectedLoginState = trustedLogin
                ? "logged_in"
                : loginState(from: snapshot)
            if detectedLoginState != "logged_in",
                let holdings = try? snapshotAfterNavigation(
                    stages: Self.accountHoldingsNavigationStages,
                    settleSeconds: 0.5
                ),
                Self.safeHoldingsLoginLabels.allSatisfy(
                    holdings.combinedText.contains
                )
            {
                detectedLoginState = "logged_in"
            }
            return ProbeData(
                appRunning: true,
                applicationPathValid: true,
                accessibilityTrusted: true,
                loginState: detectedLoginState,
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
        guard let navigation = findNavigationElement(
            in: root,
            labels: labels,
            maxDepth: 10,
            maxNodes: 1_500
        ) else {
            throw BridgeFailure(
                "page_navigation_unavailable",
                "No exact labeled safe page navigation target is available"
            )
        }
        let summary = summary(of: navigation)
        try safetyPolicy.assertReadable(path: [summary])
        try press(navigation)
        Thread.sleep(forTimeInterval: 0.35)
        return try snapshot(
            root,
            path: [],
            depth: 0,
            nodeBudget: NodeBudget(remaining: Self.snapshotNodeBudget)
        )
    }

    func applicationSnapshot() throws -> AXSnapshotNode {
        try snapshot(
            applicationElement(),
            path: [],
            depth: 0,
            nodeBudget: NodeBudget(remaining: Self.snapshotNodeBudget)
        )
    }

    func snapshotForAccount() throws -> AXSnapshotNode {
        var assets: AXSnapshotNode?
        for attempt in 0 ..< Self.accountAssetNavigationAttempts {
            var candidate = try snapshotAfterNavigation(
                stages: Self.accountAssetNavigationStages,
                settleSeconds: attempt == 0 ? 1.5 : 2.5
            )
            for _ in 0 ..< 3 where !candidate.combinedText.contains("总资产")
                && !candidate.combinedText.contains("页面加载失败")
            {
                Thread.sleep(forTimeInterval: 1.0)
                candidate = try applicationSnapshot()
            }
            if candidate.combinedText.contains("页面加载失败") {
                let root = try applicationElement()
                if let reload = safeNavigationAction(
                    in: root,
                    labels: ["重新加载"],
                    maxDepth: Self.snapshotMaxDepth,
                    maxNodes: Self.snapshotNodeBudget
                ) {
                    try safetyPolicy.assertReadable(path: [reload.label])
                    try press(reload.target)
                }
                Thread.sleep(forTimeInterval: 2.5)
                candidate = try applicationSnapshot()
            }
            assets = candidate
            if candidate.combinedText.contains("总资产") {
                break
            }
        }
        guard let assets, assets.combinedText.contains("总资产") else {
            throw BridgeFailure(
                "account_page_incomplete",
                "The read-only account overview did not expose required totals"
            )
        }
        var holdings: AXSnapshotNode?
        for attempt in 0 ..< Self.accountHoldingsNavigationAttempts {
            var candidate = try snapshotAfterNavigation(
                stages: Self.accountHoldingsNavigationStages,
                settleSeconds: attempt == 0 ? 0.8 : 1.5
            )
            for _ in 0 ..< Self.accountHoldingsPollCount
                where !Self.holdingsSnapshotReady(candidate)
            {
                Thread.sleep(forTimeInterval: 1.0)
                candidate = try applicationSnapshot()
            }
            holdings = candidate
            if Self.holdingsSnapshotReady(candidate) {
                break
            }
        }
        guard let holdings else {
            throw BridgeFailure(
                "account_holdings_incomplete",
                "The read-only holdings page did not expose a snapshot"
            )
        }
        return AXSnapshotNode(
            summary: AXNodeSummary(
                role: "AXGroup",
                title: "易淘金账户",
                label: nil,
                value: nil
            ),
            children: [assets, holdings]
        )
    }

    private static func holdingsSnapshotReady(
        _ snapshot: AXSnapshotNode
    ) -> Bool {
        let text = snapshot.combinedText
        let populated = Self.safeHoldingsLoginLabels.allSatisfy(
            text.contains
        ) && !YitaojinReader.stockCodes(in: text).isEmpty
        return populated
            || text.contains("暂无持仓")
            || text.contains("持仓数量 0")
    }

    private func navigateToWatchlist() throws -> (
        root: AXUIElement,
        navigation: AXNodeSummary
    ) {
        let root = try applicationElement()
        guard let navigation = findNavigationElement(
            in: root,
            labels: ["自选股", "自选"],
            maxDepth: 10,
            maxNodes: 1_500
        ) else {
            throw BridgeFailure(
                "watchlist_page_unavailable",
                "The self-selected list navigation target is unavailable"
            )
        }
        let navigationSummary = summary(of: navigation)
        try safetyPolicy.assertReadable(path: [navigationSummary])
        try press(navigation)
        Thread.sleep(forTimeInterval: 0.35)
        return (root: root, navigation: navigationSummary)
    }

    private func findSearchField(in root: AXUIElement) -> AXUIElement? {
        findElement(
            in: root,
            maxDepth: 10,
            maxNodes: 2_000
        ) { node in
            let roles = ["AXSearchField", "AXTextField", "AXComboBox"]
            let labels = ["搜索", "股票", "证券", "代码"]
            return roles.contains(node.role)
                && labels.contains(where: node.text.contains)
        }
    }

    private func setValue(_ value: String, on element: AXUIElement) throws {
        var settable = DarwinBoolean(false)
        guard AXUIElementIsAttributeSettable(
            element,
            kAXValueAttribute as CFString,
            &settable
        ) == .success, settable.boolValue else {
            throw BridgeFailure(
                "ui_action_unavailable",
                "The safe watchlist search field is not editable"
            )
        }
        guard AXUIElementSetAttributeValue(
            element,
            kAXValueAttribute as CFString,
            value as CFTypeRef
        ) == .success else {
            throw BridgeFailure(
                "ui_action_failed",
                "The exact stock code could not be entered safely"
            )
        }
    }

    private func actionLabels(for command: BridgeCommand) -> [String] {
        command == .addWatchlist
            ? ["添加自选", "加自选"]
            : ["删除自选", "移除自选", "取消自选"]
    }

    private func findSafeWatchlistAction(
        in root: AXUIElement,
        navigation: AXNodeSummary,
        command: BridgeCommand,
        code: String,
        environment: [String: String]
    ) throws -> AXUIElement {
        let labels = actionLabels(for: command)
        let actions = findElements(
            in: root,
            maxDepth: 12,
            maxNodes: 2_500
        ) { node in
            labels.contains(where: node.text.contains)
                && hasPressableRole(node)
        }
        var safeActions: [AXUIElement] = []
        for action in actions {
            var context = action
            for _ in 0 ..< 4 {
                let contextSnapshot = try snapshot(
                    context,
                    path: [],
                    depth: 0,
                    nodeBudget: NodeBudget(remaining: 350)
                )
                let summaries = [navigation]
                    + contextSnapshot.flattened.map(\.summary)
                if YitaojinReader.parseWatchlist(
                    from: contextSnapshot
                ).codes.contains(code) {
                    try safetyPolicy.assertWriteAllowed(
                        command: command,
                        code: code,
                        targetPath: summaries,
                        environment: environment
                    )
                    safeActions.append(action)
                    break
                }
                guard let parent = parent(of: context) else {
                    break
                }
                context = parent
            }
        }
        try safetyPolicy.assertUniqueWriteTarget(count: safeActions.count)
        return safeActions[0]
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
        if text.contains("退出交易账号")
            || text.contains("锁定账号")
            || text.contains("总资产")
            || text.contains("可用资金")
            || text.contains("我的持仓")
        {
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

    private func findNavigationElement(
        in root: AXUIElement,
        labels: [String],
        maxDepth: Int,
        maxNodes: Int
    ) -> AXUIElement? {
        findElement(
            in: root,
            maxDepth: maxDepth,
            maxNodes: maxNodes
        ) { node in
            let exactTexts = [node.title, node.label]
                .compactMap { $0 }
            return exactTexts.contains(where: labels.contains)
                && hasPressableRole(node)
        }
    }

    private func snapshotAfterNavigation(
        stages: [[String]],
        settleSeconds: TimeInterval
    ) throws -> AXSnapshotNode {
        for labels in stages {
            let root = try applicationElement()
            guard let action = safeNavigationAction(
                in: root,
                labels: labels,
                maxDepth: 14,
                maxNodes: 2_500
            ) else {
                throw BridgeFailure(
                    "page_navigation_unavailable",
                    "No exact labeled safe page navigation target is available"
                )
            }
            try safetyPolicy.assertReadable(path: [action.label])
            try press(action.target)
            Thread.sleep(forTimeInterval: settleSeconds)
        }
        return try applicationSnapshot()
    }

    private func safeNavigationAction(
        in root: AXUIElement,
        labels: [String],
        maxDepth: Int,
        maxNodes: Int
    ) -> (target: AXUIElement, label: AXNodeSummary)? {
        let matches = findElements(
            in: root,
            maxDepth: maxDepth,
            maxNodes: maxNodes
        ) { node in
            [node.title, node.label, node.value]
                .compactMap { $0 }
                .contains(where: labels.contains)
        }
        for match in matches {
            let label = summary(of: match)
            var target: AXUIElement? = match
            for _ in 0 ..< 3 {
                guard let candidate = target else {
                    break
                }
                if supportsPress(candidate) {
                    return (target: candidate, label: label)
                }
                target = parent(of: candidate)
            }
        }
        return nil
    }

    private func hasPressableRole(_ node: AXNodeSummary) -> Bool {
        let pressableRoles = [
            "AXButton",
            "AXCheckBox",
            "AXRadioButton",
            "AXTab",
            "AXMenuItem",
            "AXLink",
        ]
        return pressableRoles.contains(node.role)
    }

    private func findElement(
        in root: AXUIElement,
        maxDepth: Int,
        maxNodes: Int,
        matching predicate: (AXNodeSummary) -> Bool
    ) -> AXUIElement? {
        findElements(
            in: root,
            maxDepth: maxDepth,
            maxNodes: maxNodes,
            matching: predicate
        ).first
    }

    private func findElements(
        in root: AXUIElement,
        maxDepth: Int,
        maxNodes: Int,
        matching predicate: (AXNodeSummary) -> Bool
    ) -> [AXUIElement] {
        var remaining = maxNodes
        var matches: [AXUIElement] = []

        func visit(_ element: AXUIElement, depth: Int) {
            guard depth <= maxDepth, remaining > 0 else {
                return
            }
            remaining -= 1
            let node = summary(of: element)
            guard !safetyPolicy.isForbidden(node) else {
                return
            }
            if predicate(node) {
                matches.append(element)
            }
            for child in children(of: element) {
                visit(child, depth: depth + 1)
            }
        }

        visit(root, depth: 0)
        return matches
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

    private func supportsPress(_ element: AXUIElement) -> Bool {
        var rawActions: CFArray?
        guard
            AXUIElementCopyActionNames(element, &rawActions) == .success,
            let actions = rawActions as? [String]
        else {
            return false
        }
        return actions.contains(kAXPressAction as String)
    }

    private func snapshot(
        _ element: AXUIElement,
        path: [AXNodeSummary],
        depth: Int,
        nodeBudget: NodeBudget
    ) throws -> AXSnapshotNode {
        guard depth <= Self.snapshotMaxDepth else {
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
        let role = stringAttribute(element, kAXRoleAttribute) ?? "AXUnknown"
        var result: [AXUIElement] = []
        for attribute in Self.childTraversalAttributes(forRole: role) {
            var rawValue: CFTypeRef?
            guard
                AXUIElementCopyAttributeValue(
                    element,
                    attribute as CFString,
                    &rawValue
                ) == .success,
                let children = rawValue as? [AXUIElement]
            else {
                continue
            }
            for child in children where !result.contains(
                where: { CFEqual($0, child) }
            ) {
                result.append(child)
            }
        }
        if role == "AXRow" {
            return Array(result.prefix(Self.rowChildLimit))
        }
        return result
    }

    private func parent(of element: AXUIElement) -> AXUIElement? {
        var rawValue: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(
                element,
                kAXParentAttribute as CFString,
                &rawValue
            ) == .success,
            let parent = rawValue
        else {
            return nil
        }
        return (parent as! AXUIElement)
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

extension AXClient: WatchlistUiClient {
    func readWatchlistCodes() throws -> Set<String> {
        let snapshot = try snapshotForPage(labels: ["自选股", "自选"])
        return Set(YitaojinReader.parseWatchlist(from: snapshot).codes)
    }

    func performWatchlistMutation(
        command: BridgeCommand,
        code: String,
        environment: [String: String]
    ) throws {
        try safetyPolicy.assertWriteEnabled(
            command: command,
            code: code,
            environment: environment
        )
        let page = try navigateToWatchlist()
        if command == .addWatchlist {
            guard let searchField = findSearchField(in: page.root) else {
                throw BridgeFailure(
                    "watchlist_search_unavailable",
                    "A labeled Accessibility search field is required"
                )
            }
            try setValue(code, on: searchField)
            Thread.sleep(forTimeInterval: 0.35)
        }
        let action = try findSafeWatchlistAction(
            in: page.root,
            navigation: page.navigation,
            command: command,
            code: code,
            environment: environment
        )
        try press(action)
        Thread.sleep(forTimeInterval: 0.35)
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
