// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "YitaojinBridge",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "yitaojin-bridge", targets: ["YitaojinBridge"]),
    ],
    targets: [
        .executableTarget(
            name: "YitaojinBridge",
            linkerSettings: [
                .linkedFramework("ApplicationServices"),
                .linkedFramework("AppKit"),
            ]
        ),
    ]
)
