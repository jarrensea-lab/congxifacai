import Foundation

func writeEnvelope<T: Encodable & Sendable>(_ envelope: BridgeEnvelope<T>) throws {
    let data = try JSONEncoder.bridgeEncoder.encode(envelope)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
}

func writeError(
    command: String,
    capturedAt: String,
    failure: BridgeFailure
) {
    let envelope = BridgeErrorEnvelope(
        command: command,
        capturedAt: capturedAt,
        error: failure
    )
    if let data = try? JSONEncoder.bridgeEncoder.encode(envelope) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([0x0A]))
    }
}

let capturedAt = BridgeClock.nowText()
let requestData = FileHandle.standardInput.readDataToEndOfFile()
var commandText = "unknown"

do {
    let request = try BridgeRequest.decode(requestData)
    commandText = request.command.rawValue
    let client = AXClient()
    let reader = YitaojinReader(client: client)

    switch request.command {
    case .probe:
        try writeEnvelope(
            BridgeEnvelope(
                command: .probe,
                capturedAt: capturedAt,
                data: client.probe()
            )
        )
    case .readAccount:
        let probe = client.probe()
        guard probe.loginState == "logged_in" else {
            throw BridgeFailure(
                probe.loginState == "not_logged_in" ? "not_logged_in" : "login_state_unknown",
                "The broker login state is not safely confirmed"
            )
        }
        try writeEnvelope(
            BridgeEnvelope(
                command: .readAccount,
                capturedAt: capturedAt,
                data: try reader.readAccount(
                    fingerprintSalt: request.fingerprintSalt(),
                    capturedAt: capturedAt
                )
            )
        )
    case .readWatchlist:
        try writeEnvelope(
            BridgeEnvelope(
                command: .readWatchlist,
                capturedAt: capturedAt,
                data: try reader.readWatchlist()
            )
        )
    case .readQuotes:
        try writeEnvelope(
            BridgeEnvelope(
                command: .readQuotes,
                capturedAt: capturedAt,
                data: try reader.readQuotes(
                    codes: request.validatedCodes(),
                    capturedAt: capturedAt
                )
            )
        )
    }
} catch let failure as BridgeFailure {
    writeError(command: commandText, capturedAt: capturedAt, failure: failure)
    exit(2)
} catch {
    writeError(
        command: commandText,
        capturedAt: capturedAt,
        failure: BridgeFailure(
            "internal_error",
            "The bridge failed without exposing application data"
        )
    )
    exit(3)
}
