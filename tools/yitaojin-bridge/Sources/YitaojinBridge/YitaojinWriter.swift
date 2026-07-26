import Foundation

protocol WatchlistUiClient: AnyObject {
    func readWatchlistCodes() throws -> Set<String>

    func performWatchlistMutation(
        command: BridgeCommand,
        code: String,
        environment: [String: String]
    ) throws
}

final class YitaojinWriter {
    private let client: WatchlistUiClient
    private let environment: [String: String]
    private let safetyPolicy = SafetyPolicy()

    init(
        client: WatchlistUiClient,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) {
        self.client = client
        self.environment = environment
    }

    func mutate(
        command: BridgeCommand,
        code: String
    ) throws -> WatchlistMutationData {
        try safetyPolicy.assertWriteEnabled(
            command: command,
            code: code,
            environment: environment
        )
        let before = try client.readWatchlistCodes()
        if command == .addWatchlist, before.contains(code) {
            return WatchlistMutationData(
                code: code,
                state: "already_present",
                confirmed: true
            )
        }
        if command == .removeWatchlist, !before.contains(code) {
            return WatchlistMutationData(
                code: code,
                state: "already_absent",
                confirmed: true
            )
        }

        try client.performWatchlistMutation(
            command: command,
            code: code,
            environment: environment
        )
        let after = try client.readWatchlistCodes()
        let confirmed = command == .addWatchlist
            ? after.contains(code)
            : !after.contains(code)
        guard confirmed else {
            throw BridgeFailure(
                "watchlist_confirmation_failed",
                "The requested watchlist mutation was not confirmed by a fresh read"
            )
        }
        return WatchlistMutationData(
            code: code,
            state: command == .addWatchlist ? "added" : "removed",
            confirmed: true
        )
    }
}
