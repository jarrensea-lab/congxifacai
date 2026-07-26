import Foundation
import PackagePlugin

@main
struct YitaojinSafetyCheckPlugin: BuildToolPlugin {
    func createBuildCommands(
        context: PluginContext,
        target: Target
    ) async throws -> [Command] {
        let repoRoot = context.package.directoryURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let wrapper = repoRoot
            .appending(path: "scripts", directoryHint: .isDirectory)
            .appending(path: "test-yitaojin-bridge-swiftpm.zsh")
        let selfTestScript = repoRoot
            .appending(path: "scripts", directoryHint: .isDirectory)
            .appending(path: "test-yitaojin-bridge.sh")
        let outputFile = context.pluginWorkDirectoryURL
            .appending(path: "YitaojinSafetyChecksPassed49.swift")
        let sourceDirectories = [
            context.package.directoryURL
                .appending(path: "Sources/YitaojinBridge", directoryHint: .isDirectory),
            context.package.directoryURL
                .appending(path: "SelfTests", directoryHint: .isDirectory),
        ]
        let sourceFiles = try sourceDirectories.flatMap { directory in
            try FileManager.default
                .contentsOfDirectory(
                    at: directory,
                    includingPropertiesForKeys: nil
                )
                .filter { $0.pathExtension == "swift" }
        }

        return [
            .buildCommand(
                displayName: "Verify 49 Swift bridge checks passed",
                executable: URL(fileURLWithPath: "/bin/zsh"),
                arguments: [wrapper.path, outputFile.path],
                inputFiles: [wrapper, selfTestScript] + sourceFiles.sorted {
                    $0.path < $1.path
                },
                outputFiles: [outputFile]
            ),
        ]
    }
}
