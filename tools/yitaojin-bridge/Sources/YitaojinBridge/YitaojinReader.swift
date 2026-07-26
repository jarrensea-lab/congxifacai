import CryptoKit
import Foundation

final class YitaojinReader {
    private let client: AXClient

    init(client: AXClient = AXClient()) {
        self.client = client
    }

    func readAccount(
        fingerprintSalt: Data,
        capturedAt: String
    ) throws -> AccountData {
        let root = try client.snapshotForPage(labels: ["我的资产", "资产", "持仓"])
        return try Self.parseAccount(
            from: root,
            fingerprintSalt: fingerprintSalt,
            capturedAt: capturedAt
        )
    }

    func readWatchlist() throws -> WatchlistData {
        let root = try client.snapshotForPage(labels: ["自选股", "自选"])
        return Self.parseWatchlist(from: root)
    }

    func readQuotes(
        codes: [String],
        capturedAt: String
    ) throws -> QuotesData {
        let root = try client.snapshotForPage(labels: ["自选股", "自选", "我的持仓"])
        var quotes: [QuoteData] = []
        var found = Set<String>()
        for row in root.flattened where row.summary.role == "AXRow" {
            guard
                let code = Self.stockCodes(in: row.combinedText).first,
                codes.contains(code),
                let price = Self.firstValue(in: row, labels: ["现价", "最新价"])
            else {
                continue
            }
            found.insert(code)
            quotes.append(
                QuoteData(
                    code: code,
                    capturedAt: capturedAt,
                    marketTime: Self.firstValue(
                        in: row,
                        labels: ["行情时间", "时间"]
                    ) ?? capturedAt,
                    price: price,
                    changePct: Self.firstValue(in: row, labels: ["涨跌幅"]),
                    volume: Self.firstValue(in: row, labels: ["成交量"]),
                    amount: Self.firstValue(in: row, labels: ["成交额"]),
                    high: Self.firstValue(in: row, labels: ["最高"]),
                    low: Self.firstValue(in: row, labels: ["最低"]),
                    previousClose: Self.firstValue(in: row, labels: ["昨收"]),
                    status: Self.firstValue(in: row, labels: ["状态"]) ?? "normal"
                )
            )
        }
        return QuotesData(
            quotes: quotes.sorted { $0.code < $1.code },
            missingCodes: codes.filter { !found.contains($0) }.sorted()
        )
    }

    static func parseAccount(
        from root: AXSnapshotNode,
        fingerprintSalt: Data,
        capturedAt: String
    ) throws -> AccountData {
        guard fingerprintSalt.count == 32 else {
            throw BridgeFailure(
                "invalid_fingerprint_salt",
                "The account fingerprint salt must contain 32 bytes"
            )
        }
        guard
            let totalAssets = firstValue(in: root, labels: ["总资产"]),
            let availableCash = firstValue(
                in: root,
                labels: ["可用资金", "可用金额"]
            ),
            let frozenCash = firstValue(
                in: root,
                labels: ["冻结资金", "冻结金额"]
            ),
            let stableIdentity = firstValue(
                in: root,
                labels: ["资金账号", "客户号", "账号"]
            )
        else {
            throw BridgeFailure(
                "incomplete_account_snapshot",
                "Required labeled account fields are missing"
            )
        }
        var positions: [BrokerPositionData] = []
        for row in root.flattened where row.summary.role == "AXRow" {
            guard let code = firstValue(in: row, labels: ["证券代码", "股票代码"]) else {
                continue
            }
            guard stockCodes(in: code).contains(code) else {
                throw BridgeFailure(
                    "invalid_position_code",
                    "A position row contains an invalid stock code"
                )
            }
            guard
                let name = firstValue(in: row, labels: ["证券名称", "股票名称"]),
                let sharesText = firstValue(in: row, labels: ["持仓数量", "股份余额"]),
                let availableText = firstValue(in: row, labels: ["可用数量", "可卖数量"]),
                let shares = Int(sharesText),
                let availableShares = Int(availableText),
                let averageCost = firstValue(in: row, labels: ["成本价", "成本"]),
                let currentPrice = firstValue(in: row, labels: ["现价", "最新价"]),
                let marketValue = firstValue(in: row, labels: ["市值", "证券市值"]),
                let unrealizedPnl = firstValue(in: row, labels: ["浮动盈亏", "参考盈亏"])
            else {
                throw BridgeFailure(
                    "incomplete_position_row",
                    "A position row is missing required labeled fields"
                )
            }
            positions.append(
                BrokerPositionData(
                    code: code,
                    name: name,
                    shares: shares,
                    availableShares: availableShares,
                    averageCost: averageCost,
                    currentPrice: currentPrice,
                    marketValue: marketValue,
                    unrealizedPnl: unrealizedPnl
                )
            )
        }
        let fingerprintInput = fingerprintSalt + Data(stableIdentity.utf8)
        let digest = SHA256.hash(data: fingerprintInput)
            .map { String(format: "%02x", $0) }
            .joined()
        let emptyConfirmed = positions.isEmpty && (
            root.combinedText.contains("暂无持仓")
                || root.combinedText.contains("持仓数量 0")
        )
        return AccountData(
            capturedAt: capturedAt,
            accountFingerprint: "sha256:\(digest)",
            totalAssets: totalAssets,
            availableCash: availableCash,
            frozenCash: frozenCash,
            emptyPositionsConfirmed: emptyConfirmed,
            positions: positions.sorted { $0.code < $1.code }
        )
    }

    static func parseWatchlist(from root: AXSnapshotNode) -> WatchlistData {
        let codes = Set(
            root.flattened.compactMap { node in
                exactStockCode(in: node.summary)
            }
        )
        return WatchlistData(codes: codes.sorted())
    }

    static func exactStockCode(in summary: AXNodeSummary) -> String? {
        let pattern = try! NSRegularExpression(pattern: #"^\d{6}$"#)
        return [summary.title, summary.label, summary.value]
            .compactMap { $0 }
            .first { value in
                let range = NSRange(
                    value.startIndex ..< value.endIndex,
                    in: value
                )
                return pattern.firstMatch(in: value, range: range) != nil
            }
    }

    static func firstValue(
        in root: AXSnapshotNode,
        labels: [String]
    ) -> String? {
        for label in labels {
            if let value = root.value(forLabel: label), !value.isEmpty {
                return value
            }
        }
        return nil
    }

    static func stockCodes(in text: String) -> [String] {
        let pattern = try! NSRegularExpression(pattern: #"(?<!\d)\d{6}(?!\d)"#)
        let range = NSRange(text.startIndex ..< text.endIndex, in: text)
        return pattern.matches(in: text, range: range).compactMap { match in
            Range(match.range, in: text).map { String(text[$0]) }
        }
    }
}
