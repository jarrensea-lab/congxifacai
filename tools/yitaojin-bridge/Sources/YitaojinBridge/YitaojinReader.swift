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
        let root = try client.snapshotForAccount()
        return try Self.parseAccount(
            from: root,
            fingerprintSalt: fingerprintSalt,
            capturedAt: capturedAt
        )
    }

    func readWatchlist() throws -> WatchlistData {
        let root = try client.snapshotForWatchlist()
        return Self.parseWatchlist(from: root)
    }

    func readQuotes(
        codes: [String],
        capturedAt: String
    ) throws -> QuotesData {
        let root = try client.snapshotForWatchlist()
        return Self.parseQuotes(
            from: root,
            codes: codes,
            capturedAt: capturedAt
        )
    }

    static func parseQuotes(
        from root: AXSnapshotNode,
        codes: [String],
        capturedAt: String
    ) -> QuotesData {
        var quotes: [QuoteData] = []
        var found = Set<String>()
        for row in root.flattened where row.summary.role == "AXRow" {
            let values = normalizedRowValues(row)
            guard
                let codeIndex = values.firstIndex(where: exactCode),
                let code = values[safe: codeIndex],
                codes.contains(code),
                let price = normalizedMarketNumber(
                    Self.firstValue(in: row, labels: ["现价", "最新价"])
                        ?? values[safe: codeIndex + 2]
                )
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
                    changePct: normalizedMarketNumber(
                        Self.firstValue(in: row, labels: ["涨跌幅", "涨幅"])
                            ?? values[safe: codeIndex + 1]
                    ),
                    volume: normalizedMarketNumber(
                        Self.firstValue(in: row, labels: ["成交量"])
                            ?? values[safe: codeIndex + 4]
                    ),
                    amount: normalizedMarketNumber(
                        Self.firstValue(in: row, labels: ["成交额"])
                            ?? values[safe: codeIndex + 5]
                    ),
                    high: normalizedMarketNumber(
                        Self.firstValue(in: row, labels: ["最高"])
                            ?? values[safe: codeIndex + 10]
                    ),
                    low: normalizedMarketNumber(
                        Self.firstValue(in: row, labels: ["最低"])
                            ?? values[safe: codeIndex + 11]
                    ),
                    previousClose: normalizedMarketNumber(
                        Self.firstValue(in: row, labels: ["昨收"])
                            ?? values[safe: codeIndex + 12]
                    ),
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
        let totalAssets = firstValue(in: root, labels: ["总资产"])
            ?? firstCapture(
                in: root.combinedText,
                pattern: #"总资产(?:（元）|\(元\))?\s*([\d,]+(?:\.\d+)?)"#
            )
        let availableCash = firstValue(
            in: root,
            labels: ["可用资金", "可用金额"]
        ) ?? firstCapture(
            in: root.combinedText,
            pattern: #"现金\s*([\d,]+(?:\.\d+)?)\s*元"#
        )
        let stableIdentity = firstValue(
            in: root,
            labels: ["资金账号", "客户号", "账号"]
        ) ?? firstCapture(
            in: root.combinedText,
            pattern: #"(\*{3,}\d{4}\s+(?:普通|信用|港股通))"#
        )
        guard
            let rawTotalAssets = totalAssets,
            let rawAvailableCash = availableCash,
            let stableIdentity
        else {
            throw BridgeFailure(
                "incomplete_account_snapshot",
                "Required labeled account fields are missing"
            )
        }
        let normalizedTotalAssets = normalizedNumber(rawTotalAssets)
        let normalizedAvailableCash = normalizedNumber(rawAvailableCash)
        var positions: [BrokerPositionData] = []
        let rows = root.flattened.filter { $0.summary.role == "AXRow" }
        let positionNames = rows.compactMap(positionName(in:))
        var positionNameIndex = 0
        for row in rows {
            let values = normalizedRowValues(row)
            guard
                let codeIndex = values.firstIndex(where: exactCode),
                let code = values[safe: codeIndex]
            else {
                continue
            }
            let positionValues = Array(values[codeIndex...])
            let labeledName = firstValue(
                in: row,
                labels: ["证券名称", "股票名称"]
            )
            let labeledShares = firstValue(
                in: row,
                labels: ["持仓数量", "股份余额"]
            )
            let labeledAvailable = firstValue(
                in: row,
                labels: ["可用数量", "可卖数量"]
            )
            let name = labeledName
                ?? positionNames[safe: positionNameIndex]
            let sharesText = labeledShares
                ?? positionValues[safe: 2]
            let availableText = labeledAvailable
                ?? positionValues[safe: 1]
            let averageCost = firstValue(
                in: row,
                labels: ["成本价", "成本"]
            ) ?? positionValues[safe: 3]
            let unrealizedPnl = firstValue(
                in: row,
                labels: ["浮动盈亏", "参考盈亏"]
            ) ?? positionValues[safe: 4]
            let marketValue = firstValue(
                in: row,
                labels: ["市值", "证券市值", "持仓市值"]
            ) ?? positionValues[safe: 7]
            let labeledCurrentPrice = firstValue(
                in: row,
                labels: ["现价", "最新价"]
            )
            let currentPrice = numericValue(
                labeledCurrentPrice,
                fallback: positionValues[safe: 10]
            ) ?? reconciledUnitPrice(
                marketValue: marketValue,
                shares: sharesText
            )
            guard
                let name,
                let sharesText,
                let availableText,
                let shares = Int(normalizedNumber(sharesText)),
                let availableShares = Int(normalizedNumber(availableText)),
                let averageCost,
                let currentPrice,
                let marketValue,
                let unrealizedPnl
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
                    averageCost: normalizedNumber(averageCost),
                    currentPrice: normalizedNumber(currentPrice),
                    marketValue: normalizedNumber(marketValue),
                    unrealizedPnl: normalizedNumber(unrealizedPnl)
                )
            )
            positionNameIndex += 1
        }
        let frozenCash = firstValue(
            in: root,
            labels: ["冻结资金", "冻结金额"]
        ) ?? reconciledFrozenCash(
            totalAssets: normalizedTotalAssets,
            availableCash: normalizedAvailableCash,
            positions: positions
        )
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
            totalAssets: normalizedTotalAssets,
            availableCash: normalizedAvailableCash,
            frozenCash: normalizedNumber(frozenCash),
            emptyPositionsConfirmed: emptyConfirmed,
            positions: positions.sorted { $0.code < $1.code }
        )
    }

    static func parseWatchlist(from root: AXSnapshotNode) -> WatchlistData {
        let rowCodes = Set(
            root.flattened
                .filter { $0.summary.role == "AXRow" }
                .compactMap { row in
                    row.flattened.compactMap { node in
                        exactStockCode(in: node.summary)
                    }.first
                }
        )
        if !rowCodes.isEmpty {
            return WatchlistData(codes: rowCodes.sorted())
        }
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

    private static func firstCapture(
        in text: String,
        pattern: String
    ) -> String? {
        let expression = try! NSRegularExpression(pattern: pattern)
        let range = NSRange(text.startIndex ..< text.endIndex, in: text)
        guard
            let match = expression.firstMatch(in: text, range: range),
            match.numberOfRanges > 1,
            let capture = Range(match.range(at: 1), in: text)
        else {
            return nil
        }
        return String(text[capture])
    }

    private static func normalizedNumber(_ value: String) -> String {
        value.replacingOccurrences(of: ",", with: "")
    }

    private static func normalizedMarketNumber(_ value: String?) -> String? {
        guard var value else {
            return nil
        }
        value = normalizedNumber(value)
            .replacingOccurrences(of: "%", with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        var multiplier = Decimal(1)
        if value.hasSuffix("万") {
            value.removeLast()
            multiplier = Decimal(10_000)
        } else if value.hasSuffix("亿") {
            value.removeLast()
            multiplier = Decimal(100_000_000)
        }
        guard
            let number = Decimal(string: value),
            number.isFinite
        else {
            return nil
        }
        return NSDecimalNumber(decimal: number * multiplier).stringValue
    }

    private static func numericValue(
        _ preferred: String?,
        fallback: String?
    ) -> String? {
        if let preferred, isFiniteNumber(preferred) {
            return preferred
        }
        return fallback
    }

    private static func isFiniteNumber(_ value: String) -> Bool {
        let normalized = normalizedNumber(value)
        return normalized.range(
            of: #"^-?\d+(?:\.\d+)?$"#,
            options: .regularExpression
        ) != nil
    }

    private static func reconciledUnitPrice(
        marketValue: String?,
        shares: String?
    ) -> String? {
        guard
            let marketValue,
            let shares,
            let normalizedMarketValue = Decimal(
                string: normalizedNumber(marketValue)
            ),
            let normalizedShares = Decimal(string: normalizedNumber(shares)),
            normalizedShares > 0
        else {
            return nil
        }
        return NSDecimalNumber(
            decimal: normalizedMarketValue / normalizedShares
        ).stringValue
    }

    private static func exactCode(_ value: String) -> Bool {
        value.count == 6 && value.allSatisfy(\.isNumber)
    }

    private static func normalizedRowValues(
        _ row: AXSnapshotNode
    ) -> [String] {
        row.children.compactMap { cell in
            cell.flattened.lazy.compactMap { node in
                [
                    node.summary.value,
                    node.summary.title,
                    node.summary.label,
                ]
                .compactMap({ $0 })
                .first(where: { !$0.isEmpty })
            }.first
        }
    }

    private static func positionName(in row: AXSnapshotNode) -> String? {
        let values = normalizedRowValues(row)
        guard
            !values.contains(where: exactCode),
            values.count <= 3
        else {
            return nil
        }
        let headerFragments = [
            "序号",
            "名称",
            "代码",
            "数量",
            "成本",
            "盈亏",
            "市值",
            "现价",
            "涨跌",
            "地区",
        ]
        return values.first { value in
            value.range(
                of: #"^[\p{Han}A-Za-z][\p{Han}A-Za-z0-9 ]{1,19}$"#,
                options: .regularExpression
            ) != nil
                && !headerFragments.contains(where: value.contains)
        }
    }

    private static func reconciledFrozenCash(
        totalAssets: String,
        availableCash: String,
        positions: [BrokerPositionData]
    ) -> String {
        guard
            let total = Decimal(string: totalAssets),
            let available = Decimal(string: availableCash)
        else {
            return "0"
        }
        let marketValue = positions.reduce(Decimal.zero) { partial, position in
            partial + (Decimal(string: position.marketValue) ?? .zero)
        }
        let remainder = total - available - marketValue
        return NSDecimalNumber(decimal: remainder).stringValue
    }
}

private extension Collection {
    subscript(safe index: Index) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}
