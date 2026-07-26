import Foundation

enum BridgeCommand: String, Codable, Sendable {
    case probe
    case readAccount = "read_account"
    case readWatchlist = "read_watchlist"
    case readQuotes = "read_quotes"
}

struct BridgeFailure: Error, Codable, Equatable, Sendable {
    let code: String
    let message: String

    init(_ code: String, _ message: String) {
        self.code = code
        self.message = message
    }
}

struct BridgePayload: Codable, Sendable {
    let codes: [String]?
    let fingerprintSalt: String?

    init(codes: [String]? = nil, fingerprintSalt: String? = nil) {
        self.codes = codes
        self.fingerprintSalt = fingerprintSalt
    }
}

struct BridgeRequest: Codable, Sendable {
    let schemaVersion: Int
    let command: BridgeCommand
    let payload: BridgePayload?

    static func decode(_ data: Data) throws -> BridgeRequest {
        do {
            let request = try JSONDecoder().decode(BridgeRequest.self, from: data)
            guard request.schemaVersion == 1 else {
                throw BridgeFailure(
                    "unsupported_schema",
                    "Only schemaVersion 1 is supported"
                )
            }
            return request
        } catch let failure as BridgeFailure {
            throw failure
        } catch {
            throw BridgeFailure("invalid_request", "Request JSON is invalid")
        }
    }

    func validatedCodes() throws -> [String] {
        let values = payload?.codes ?? []
        let codePattern = try! NSRegularExpression(pattern: #"^\d{6}$"#)
        var result = Set<String>()
        for value in values {
            let range = NSRange(value.startIndex ..< value.endIndex, in: value)
            guard codePattern.firstMatch(in: value, range: range) != nil else {
                throw BridgeFailure(
                    "invalid_stock_code",
                    "Quote codes must be canonical 6-digit strings"
                )
            }
            result.insert(value)
        }
        guard result.count <= 32 else {
            throw BridgeFailure(
                "quote_scope_too_large",
                "At most 32 quote codes are allowed"
            )
        }
        return result.sorted()
    }

    func fingerprintSalt() throws -> Data {
        guard
            let encoded = payload?.fingerprintSalt,
            let salt = Data(base64Encoded: encoded),
            salt.count == 32
        else {
            throw BridgeFailure(
                "invalid_fingerprint_salt",
                "read_account requires a 32-byte base64 fingerprint salt"
            )
        }
        return salt
    }
}

struct BridgeEnvelope<DataType: Encodable & Sendable>: Encodable, Sendable {
    let schemaVersion = 1
    let ok = true
    let command: BridgeCommand
    let capturedAt: String
    let data: DataType
}

struct BridgeErrorEnvelope: Encodable, Sendable {
    let schemaVersion = 1
    let ok = false
    let command: String
    let capturedAt: String
    let error: BridgeFailure
}

struct ProbeData: Codable, Sendable {
    let appRunning: Bool
    let applicationPathValid: Bool
    let accessibilityTrusted: Bool
    let loginState: String
    let interfaceSignature: String?
}

struct BrokerPositionData: Codable, Sendable {
    let code: String
    let name: String
    let shares: Int
    let availableShares: Int
    let averageCost: String
    let currentPrice: String
    let marketValue: String
    let unrealizedPnl: String
}

struct AccountData: Codable, Sendable {
    let capturedAt: String
    let accountFingerprint: String
    let totalAssets: String
    let availableCash: String
    let frozenCash: String
    let emptyPositionsConfirmed: Bool
    let positions: [BrokerPositionData]
}

struct WatchlistData: Codable, Sendable {
    let codes: [String]
}

struct QuoteData: Codable, Sendable {
    let code: String
    let capturedAt: String
    let marketTime: String
    let price: String
    let changePct: String?
    let volume: String?
    let amount: String?
    let high: String?
    let low: String?
    let previousClose: String?
    let status: String
}

struct QuotesData: Codable, Sendable {
    let quotes: [QuoteData]
    let missingCodes: [String]
}

extension JSONEncoder {
    static var bridgeEncoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return encoder
    }
}

enum BridgeClock {
    static func nowText() -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "Asia/Shanghai")
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ssXXX"
        return formatter.string(from: Date())
    }
}
