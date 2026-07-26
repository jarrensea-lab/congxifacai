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

do {
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
