import Foundation

private var failures: [String] = []
private var passes = 0

private func expect(
    _ condition: @autoclosure () -> Bool,
    _ message: String
) {
    if condition() {
        passes += 1
    } else {
        failures.append(message)
    }
}

private func expectBridgeFailure(
    _ message: String,
    operation: () throws -> Void
) {
    do {
        try operation()
        failures.append(message)
    } catch is BridgeFailure {
        passes += 1
    } catch {
        failures.append("\(message): wrong error type")
    }
}

private extension AXSnapshotNode {
    static func labeled(_ title: String, value: String) -> AXSnapshotNode {
        AXSnapshotNode(
            summary: AXNodeSummary(
                role: "AXStaticText",
                title: title,
                label: nil,
                value: value
            ),
            children: []
        )
    }
}

private final class FakeWatchlistUiClient: WatchlistUiClient {
    var codes: Set<String>
    var actionCalls: [(BridgeCommand, String)] = []
    var confirmMutations = true

    init(codes: Set<String>) {
        self.codes = codes
    }

    func readWatchlistCodes() throws -> Set<String> {
        codes
    }

    func performWatchlistMutation(
        command: BridgeCommand,
        code: String,
        environment: [String: String]
    ) throws {
        actionCalls.append((command, code))
        guard confirmMutations else {
            return
        }
        if command == .addWatchlist {
            codes.insert(code)
        } else if command == .removeWatchlist {
            codes.remove(code)
        }
    }
}

do {
    expect(
        AXClient.childTraversalAttributes(forRole: "AXTable")
            == ["AXVisibleRows", "AXRows", "AXChildren"],
        "AX tables do not expose virtualized rows to the snapshot traversal"
    )
    expect(
        AXClient.childTraversalAttributes(forRole: "AXGroup")
            == ["AXChildren"],
        "non-table Accessibility traversal changed unexpectedly"
    )
    expect(
        AXClient.snapshotMaxDepth == 24,
        "snapshot depth does not reach GF-Trader's nested virtualized rows"
    )
    expect(
        AXClient.snapshotNodeBudget == 7_500,
        "snapshot node budget is too small for the bounded GF-Trader table"
    )
    expect(
        AXClient.rowChildLimit == 16,
        "Accessibility rows are not bounded before sensitive trailing columns"
    )
    expect(
        AXClient.accountAssetNavigationStages
            == [["我的"], ["资产全景"]],
        "account asset navigation left the safe read-only route"
    )
    expect(
        AXClient.accountHoldingsNavigationStages
            == [["自选股", "自选"], ["我的持仓"]],
        "account holdings navigation left the watchlist route"
    )
    expect(
        AXClient.safeLoginIndicators == ["锁定账号"],
        "login detection no longer uses the safe account-lock indicator"
    )
    expect(
        AXClient.safeHoldingsLoginLabels
            == ["普通持仓", "可用数量", "成本价"],
        "holdings login fallback no longer requires the full safe field set"
    )
    expect(
        AXClient.accountAssetNavigationAttempts == 2,
        "account capture no longer retries the read-only asset route once"
    )
    expect(
        AXClient.accountHoldingsNavigationAttempts == 2
            && AXClient.accountHoldingsPollCount == 5,
        "account capture no longer waits for the virtualized holdings table"
    )

    expect(
        Set(BridgeCommand.allCases.map(\.rawValue)) == [
            "probe",
            "read_account",
            "read_watchlist",
            "read_quotes",
            "add_watchlist",
            "remove_watchlist",
        ],
        "bridge command allowlist changed"
    )

    let request = try BridgeRequest.decode(
        Data(
            """
            {
              "schemaVersion": 1,
              "command": "read_quotes",
              "payload": {"codes": ["600000", "000001", "600000"]}
            }
            """.utf8
        )
    )
    expect(request.command == .readQuotes, "read_quotes command did not decode")
    let normalizedCodes = try request.validatedCodes()
    expect(
        normalizedCodes == ["000001", "600000"],
        "read_quotes codes were not deduplicated and sorted"
    )

    expectBridgeFailure("unsafe buy command was accepted") {
        _ = try BridgeRequest.decode(
            Data(
                """
                {"schemaVersion":1,"command":"buy","payload":{"codes":["600000"]}}
                """.utf8
            )
        )
    }

    let addRequest = try BridgeRequest.decode(
        Data(
            """
            {
              "schemaVersion": 1,
              "command": "add_watchlist",
              "payload": {"code": "600000"}
            }
            """.utf8
        )
    )
    expect(addRequest.command == .addWatchlist, "add_watchlist did not decode")
    let addCode = try addRequest.validatedSingleCode()
    expect(
        addCode == "600000",
        "add_watchlist code was not validated"
    )

    let removeRequest = try BridgeRequest.decode(
        Data(
            """
            {
              "schemaVersion": 1,
              "command": "remove_watchlist",
              "payload": {"code": "000001"}
            }
            """.utf8
        )
    )
    expect(
        removeRequest.command == .removeWatchlist,
        "remove_watchlist did not decode"
    )
    let removeCode = try removeRequest.validatedSingleCode()
    expect(
        removeCode == "000001",
        "remove_watchlist code was not validated"
    )

    let missingWriteCode = try BridgeRequest.decode(
        Data(
            """
            {"schemaVersion":1,"command":"add_watchlist","payload":{}}
            """.utf8
        )
    )
    expectBridgeFailure("watchlist write accepted a missing code") {
        _ = try missingWriteCode.validatedSingleCode()
    }

    let invalidWriteCode = try BridgeRequest.decode(
        Data(
            """
            {
              "schemaVersion":1,
              "command":"remove_watchlist",
              "payload":{"code":"60000A"}
            }
            """.utf8
        )
    )
    expectBridgeFailure("watchlist write accepted an invalid code") {
        _ = try invalidWriteCode.validatedSingleCode()
    }

    let codes = (0 ..< 33).map { String(format: "%06d", $0) }
    let oversizedData = try JSONSerialization.data(
        withJSONObject: [
            "schemaVersion": 1,
            "command": "read_quotes",
            "payload": ["codes": codes],
        ]
    )
    let oversized = try BridgeRequest.decode(oversizedData)
    expectBridgeFailure("more than 32 quote codes were accepted") {
        _ = try oversized.validatedCodes()
    }

    let noSalt = try BridgeRequest.decode(
        Data(
            """
            {"schemaVersion":1,"command":"read_account","payload":{}}
            """.utf8
        )
    )
    expectBridgeFailure("read_account accepted a missing fingerprint salt") {
        _ = try noSalt.fingerprintSalt()
    }

    let envelope = BridgeEnvelope(
        command: .probe,
        capturedAt: "2026-07-26T09:30:05+08:00",
        data: ProbeData(
            appRunning: true,
            applicationPathValid: true,
            accessibilityTrusted: false,
            loginState: "unknown",
            interfaceSignature: nil
        )
    )
    let object = try JSONSerialization.jsonObject(
        with: JSONEncoder.bridgeEncoder.encode(envelope)
    ) as! [String: Any]
    expect(object["schemaVersion"] as? Int == 1, "envelope schema changed")
    expect(object["ok"] as? Bool == true, "success envelope is not ok")
    expect(object["command"] as? String == "probe", "envelope command changed")

    for label in [
        "买入",
        "卖出",
        "委托下单",
        "撤单",
        "一键清仓",
        "银证转账",
        "交易密码",
        "短信验证码",
    ] {
        let path = [
            AXNodeSummary(
                role: "AXWindow",
                title: "广发易淘金",
                label: nil,
                value: nil
            ),
            AXNodeSummary(
                role: "AXButton",
                title: label,
                label: nil,
                value: nil
            ),
        ]
        expectBridgeFailure("forbidden label was readable: \(label)") {
            try SafetyPolicy().assertReadable(path: path)
        }
    }

    try SafetyPolicy().assertReadable(
        path: [
            AXNodeSummary(
                role: "AXGroup",
                title: "我的资产",
                label: nil,
                value: nil
            ),
            AXNodeSummary(
                role: "AXStaticText",
                title: "总资产",
                label: nil,
                value: "6051.25"
            ),
        ]
    )
    passes += 1

    let safeWatchlistPath = [
        AXNodeSummary(
            role: "AXGroup",
            title: "自选股",
            label: nil,
            value: nil
        ),
        AXNodeSummary(
            role: "AXButton",
            title: "添加自选",
            label: nil,
            value: "600000"
        ),
    ]
    try SafetyPolicy().assertWriteAllowed(
        command: .addWatchlist,
        code: "600000",
        targetPath: safeWatchlistPath,
        environment: ["CONGXI_YITAOJIN_WRITE_ENABLED": "true"]
    )
    passes += 1

    expectBridgeFailure("watchlist write bypassed the write-enable gate") {
        try SafetyPolicy().assertWriteAllowed(
            command: .addWatchlist,
            code: "600000",
            targetPath: safeWatchlistPath,
            environment: [:]
        )
    }

    expectBridgeFailure("watchlist write crossed into a trading target") {
        try SafetyPolicy().assertWriteAllowed(
            command: .removeWatchlist,
            code: "600000",
            targetPath: [
                AXNodeSummary(
                    role: "AXGroup",
                    title: "自选股",
                    label: nil,
                    value: nil
                ),
                AXNodeSummary(
                    role: "AXButton",
                    title: "卖出 600000",
                    label: nil,
                    value: nil
                ),
            ],
            environment: ["CONGXI_YITAOJIN_WRITE_ENABLED": "true"]
        )
    }

    expectBridgeFailure("not-logged-in App passed the login gate") {
        try SafetyPolicy().assertLoggedIn(
            ProbeData(
                appRunning: true,
                applicationPathValid: true,
                accessibilityTrusted: true,
                loginState: "not_logged_in",
                interfaceSignature: nil
            )
        )
    }
    try SafetyPolicy().assertLoggedIn(
        ProbeData(
            appRunning: true,
            applicationPathValid: true,
            accessibilityTrusted: true,
            loginState: "logged_in",
            interfaceSignature: nil
        )
    )
    passes += 1

    try SafetyPolicy().assertUniqueWriteTarget(count: 1)
    passes += 1
    expectBridgeFailure("zero matching write targets were accepted") {
        try SafetyPolicy().assertUniqueWriteTarget(count: 0)
    }
    expectBridgeFailure("ambiguous write targets were accepted") {
        try SafetyPolicy().assertUniqueWriteTarget(count: 2)
    }

    let alreadyPresentClient = FakeWatchlistUiClient(codes: ["600000"])
    let alreadyPresent = try YitaojinWriter(
        client: alreadyPresentClient,
        environment: ["CONGXI_YITAOJIN_WRITE_ENABLED": "true"]
    ).mutate(command: .addWatchlist, code: "600000")
    expect(alreadyPresent.confirmed, "already-present add was not confirmed")
    expect(
        alreadyPresent.state == "already_present",
        "already-present add returned the wrong state"
    )
    expect(
        alreadyPresentClient.actionCalls.isEmpty,
        "already-present add unnecessarily mutated the UI"
    )
    expectBridgeFailure("already-present add bypassed the write-enable gate") {
        _ = try YitaojinWriter(
            client: alreadyPresentClient,
            environment: [:]
        ).mutate(command: .addWatchlist, code: "600000")
    }

    let addClient = FakeWatchlistUiClient(codes: [])
    let added = try YitaojinWriter(
        client: addClient,
        environment: ["CONGXI_YITAOJIN_WRITE_ENABLED": "true"]
    ).mutate(command: .addWatchlist, code: "600000")
    expect(added.confirmed, "add was not reread and confirmed")
    expect(added.state == "added", "confirmed add returned the wrong state")
    expect(
        addClient.actionCalls.map(\.1) == ["600000"],
        "add did not remain scoped to one exact code"
    )

    let alreadyAbsentClient = FakeWatchlistUiClient(codes: [])
    let alreadyAbsent = try YitaojinWriter(
        client: alreadyAbsentClient,
        environment: ["CONGXI_YITAOJIN_WRITE_ENABLED": "true"]
    ).mutate(command: .removeWatchlist, code: "000001")
    expect(alreadyAbsent.confirmed, "already-absent remove was not confirmed")
    expect(
        alreadyAbsent.state == "already_absent",
        "already-absent remove returned the wrong state"
    )
    expect(
        alreadyAbsentClient.actionCalls.isEmpty,
        "already-absent remove unnecessarily mutated the UI"
    )

    let unconfirmedClient = FakeWatchlistUiClient(codes: [])
    unconfirmedClient.confirmMutations = false
    expectBridgeFailure("unconfirmed add was reported as successful") {
        _ = try YitaojinWriter(
            client: unconfirmedClient,
            environment: ["CONGXI_YITAOJIN_WRITE_ENABLED": "true"]
        ).mutate(command: .addWatchlist, code: "000001")
    }

    let root = AXSnapshotNode(
        summary: AXNodeSummary(
            role: "AXGroup",
            title: "资产",
            label: nil,
            value: nil
        ),
        children: [
            .labeled("总资产", value: "6051.25"),
            .labeled("可用资金", value: "1383.25"),
            .labeled("冻结资金", value: "0.00"),
            .labeled("资金账号", value: "masked-stable-id"),
            AXSnapshotNode(
                summary: AXNodeSummary(
                    role: "AXRow",
                    title: "持仓",
                    label: nil,
                    value: nil
                ),
                children: [
                    .labeled("证券代码", value: "000001"),
                    .labeled("证券名称", value: "测试股份"),
                    .labeled("持仓数量", value: "200"),
                    .labeled("可用数量", value: "100"),
                    .labeled("成本价", value: "10.125"),
                    .labeled("现价", value: "10.50"),
                    .labeled("市值", value: "2100.00"),
                    .labeled("浮动盈亏", value: "75.00"),
                ]
            ),
        ]
    )
    let account = try YitaojinReader.parseAccount(
        from: root,
        fingerprintSalt: Data(repeating: 7, count: 32),
        capturedAt: "2026-07-26T09:30:05+08:00"
    )
    expect(account.totalAssets == "6051.25", "account total assets parsed incorrectly")
    expect(account.positions.count == 1, "account position row was not parsed")
    expect(account.positions[0].code == "000001", "position code parsed incorrectly")
    expect(
        account.accountFingerprint.hasPrefix("sha256:"),
        "account fingerprint is not a SHA-256 digest"
    )
    expect(
        !account.accountFingerprint.contains("masked-stable-id"),
        "raw account identity leaked into fingerprint"
    )

    let unavailablePriceRoot = AXSnapshotNode(
        summary: AXNodeSummary(
            role: "AXGroup",
            title: "资产",
            label: nil,
            value: nil
        ),
        children: [
            .labeled("总资产", value: "2100.00"),
            .labeled("可用资金", value: "100.00"),
            .labeled("资金账号", value: "masked-stable-id"),
            AXSnapshotNode(
                summary: AXNodeSummary(
                    role: "AXRow",
                    title: "持仓",
                    label: nil,
                    value: nil
                ),
                children: [
                    .labeled("证券代码", value: "000001"),
                    .labeled("证券名称", value: "测试股份"),
                    .labeled("持仓数量", value: "100"),
                    .labeled("可用数量", value: "100"),
                    .labeled("成本价", value: "19.00"),
                    .labeled("现价", value: "--"),
                    .labeled("市值", value: "2000.00"),
                    .labeled("浮动盈亏", value: "100.00"),
                ]
            ),
        ]
    )
    let unavailablePriceAccount = try YitaojinReader.parseAccount(
        from: unavailablePriceRoot,
        fingerprintSalt: Data(repeating: 7, count: 32),
        capturedAt: "2026-07-26T15:10:05+08:00"
    )
    expect(
        unavailablePriceAccount.positions.first?.currentPrice == "20",
        "unavailable UI price was not reconciled from market value and shares"
    )

    let liveLayoutRoot = AXSnapshotNode(
        summary: AXNodeSummary(
            role: "AXGroup",
            title: "易淘金账户",
            label: nil,
            value: nil
        ),
        children: [
            .labeled(
                "资产概览",
                value: "总资产（元） 6,051.25 资产分布 股票 4,668.00 元 现金 1,383.25 元"
            ),
            .labeled("脱敏账号", value: "*******5159 普通"),
            AXSnapshotNode(
                summary: AXNodeSummary(
                    role: "AXRow",
                    title: nil,
                    label: nil,
                    value: nil
                ),
                children: [
                    .labeled("表头", value: "序号"),
                    .labeled("表头", value: "名称"),
                ]
            ),
            AXSnapshotNode(
                summary: AXNodeSummary(
                    role: "AXRow",
                    title: nil,
                    label: nil,
                    value: nil
                ),
                children: [
                    .labeled("表头", value: "代码"),
                    .labeled("表头", value: "可用数量"),
                    .labeled("表头", value: "当前数量"),
                    .labeled("表头", value: "成本价"),
                ]
            ),
            AXSnapshotNode(
                summary: AXNodeSummary(
                    role: "AXRow",
                    title: nil,
                    label: nil,
                    value: nil
                ),
                children: [
                    .labeled("名称", value: "长江电力"),
                ]
            ),
            AXSnapshotNode(
                summary: AXNodeSummary(
                    role: "AXRow",
                    title: nil,
                    label: nil,
                    value: nil
                ),
                children: [
                    .labeled("代码", value: "600900"),
                    .labeled("可用数量", value: "100"),
                    .labeled("当前数量", value: "100"),
                    .labeled("成本价", value: "27.2500"),
                    .labeled("浮动盈亏", value: "165.00"),
                    .labeled("盈亏比", value: "6.06%"),
                    .labeled("个股仓位", value: "47.76%"),
                    .labeled("持仓市值", value: "2,890.00"),
                    .labeled("地区", value: "北京市"),
                    .labeled("涨幅", value: "-0.14%"),
                    .labeled("报价", value: "28.90"),
                    .labeled("现价", value: "--"),
                ]
            ),
        ]
    )
    let liveLayoutAccount = try YitaojinReader.parseAccount(
        from: liveLayoutRoot,
        fingerprintSalt: Data(repeating: 8, count: 32),
        capturedAt: "2026-07-27T08:30:05+08:00"
    )
    expect(
        liveLayoutAccount.totalAssets == "6051.25",
        "live-layout total assets were not normalized"
    )
    expect(
        liveLayoutAccount.availableCash == "1383.25",
        "live-layout cash was not normalized"
    )
    expect(
        liveLayoutAccount.positions.first?.name == "长江电力",
        "live-layout position name was not paired by row order"
    )
    expect(
        liveLayoutAccount.positions.first?.shares == 100
            && liveLayoutAccount.positions.first?.availableShares == 100,
        "live-layout position quantities were not parsed"
    )
    expect(
        liveLayoutAccount.positions.first?.currentPrice == "28.90",
        "live-layout current price was not parsed: "
            + (liveLayoutAccount.positions.first?.currentPrice ?? "nil")
    )

    let watchlistRoot = AXSnapshotNode(
        summary: AXNodeSummary(
            role: "AXGroup",
            title: "自选股",
            label: nil,
            value: nil
        ),
        children: [
            .labeled("证券代码", value: "300207"),
            AXSnapshotNode(
                summary: AXNodeSummary(
                    role: "AXStaticText",
                    title: "300456",
                    label: nil,
                    value: nil
                ),
                children: []
            ),
            .labeled("指标值", value: "0.471886"),
            .labeled("成交额", value: "123456.78"),
            .labeled("混合文本", value: "股票 600000"),
        ]
    )
    let watchlist = YitaojinReader.parseWatchlist(from: watchlistRoot)
    expect(
        watchlist.codes == ["300207", "300456"],
        "watchlist parser extracted a six-digit numeric substring"
    )

    let virtualizedWatchlistRoot = AXSnapshotNode(
        summary: AXNodeSummary(
            role: "AXTable",
            title: "自选股",
            label: nil,
            value: nil
        ),
        children: [
            AXSnapshotNode(
                summary: AXNodeSummary(
                    role: "AXRow",
                    title: nil,
                    label: nil,
                    value: nil
                ),
                children: [
                    .labeled("代码", value: "000100"),
                    .labeled("代码副本", value: "000100"),
                    .labeled("成交量", value: "139976"),
                ]
            ),
        ]
    )
    expect(
        YitaojinReader.parseWatchlist(from: virtualizedWatchlistRoot).codes
            == ["000100"],
        "watchlist parser confused a six-digit quote value with the row code"
    )
} catch {
    failures.append("unexpected self-test error: \(error)")
}

if failures.isEmpty {
    print("\(passes) Swift bridge checks passed")
    exit(0)
}

for failure in failures {
    FileHandle.standardError.write(Data("FAIL: \(failure)\n".utf8))
}
exit(1)
