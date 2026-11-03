import Foundation
import AppKit

enum QuotaPaths {
    static let root = ProcessInfo.processInfo.environment["AI_QUOTA_DATA_DIR"] ?? NSHomeDirectory() + "/.swiftbar"
    static let resourceURL = Bundle.main.resourceURL ?? Bundle.main.bundleURL.appendingPathComponent("Contents/Resources")
    static var architecture: String {
        #if arch(arm64)
        return "arm64"
        #else
        return "x86_64"
        #endif
    }
    static var python: URL { resourceURL.appendingPathComponent("runtime/\(architecture)/bin/python3") }
    static func script(_ name: String) -> String { resourceURL.appendingPathComponent("scripts/" + name).path }
    static var childEnvironment: [String: String] {
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "PYTHONHOME"); env.removeValue(forKey: "PYTHONPATH")
        if FileManager.default.fileExists(atPath: "/etc/ssl/cert.pem") { env["SSL_CERT_FILE"] = "/etc/ssl/cert.pem" }
        env["PYTHONNOUSERSITE"] = "1"; env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["AI_QUOTA_APP_BUNDLE"] = Bundle.main.bundleURL.path
        env["AI_QUOTA_DATA_DIR"] = root
        return env
    }
    static func prepareDefaults() throws {
        let fm = FileManager.default
        try fm.createDirectory(atPath: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try fm.createDirectory(atPath: root + "/tokens", withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let configuration = URL(fileURLWithPath: root + "/ai_accounts_config.json")
        if !fm.fileExists(atPath: configuration.path) {
            let data = try Data(contentsOf: resourceURL.appendingPathComponent("accounts.default.json"))
            try data.write(to: configuration, options: .atomic)
            try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: configuration.path)
        }
    }
    static func selfCheck() -> Int32 {
        do {
            try prepareDefaults()
            let required = [python.path, script("fetch_quotas.py"), script("bind_account.py"), script("fetch_linksapi.py"), script("ccswitch_source.py"),
                            Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/ChromeSessionBridge").path,
                            resourceURL.appendingPathComponent("AppIcon.icns").path]
            guard required.allSatisfy({ FileManager.default.fileExists(atPath: $0) }) else {
                throw NSError(domain: "AIQuotaDashboard", code: 1, userInfo: [NSLocalizedDescriptionKey: "应用包不完整"])
            }
            let process = Process(); let pipe = Pipe()
            process.executableURL = python; process.arguments = ["-I", "-B", "-c", "import ssl, sqlite3, json, concurrent.futures; print('runtime-ready')"]
            process.environment = childEnvironment; process.standardOutput = pipe; process.standardError = pipe
            try process.run(); process.waitUntilExit()
            guard process.terminationStatus == 0 else { throw NSError(domain: "AIQuotaDashboard", code: 2) }
            print("{\"ok\":true,\"architecture\":\"\(architecture)\",\"bundled_runtime\":true}")
            return 0
        } catch {
            fputs("Self-check failed: \(error.localizedDescription)\n", stderr)
            return 1
        }
    }
}
