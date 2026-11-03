import Foundation
import Darwin

/// UI work exists only while the card can actually be seen.
final class DisplayActivity {
    private let progressInterval: TimeInterval
    private let clockInterval: TimeInterval
    private let onProgress: () -> Void
    private let onClock: () -> Void
    private var progressTimer: Timer?
    private var clockTimer: Timer?
    private var visible = false
    private var refreshing = false

    init(progressInterval: TimeInterval = 1, clockInterval: TimeInterval = 15,
         onProgress: @escaping () -> Void, onClock: @escaping () -> Void) {
        self.progressInterval = progressInterval
        self.clockInterval = clockInterval
        self.onProgress = onProgress
        self.onClock = onClock
    }
    func setVisible(_ value: Bool) {
        guard visible != value else { return }
        visible = value
        if value { onClock() }
        updateTimers()
    }
    func setRefreshing(_ value: Bool) {
        refreshing = value
        updateTimers()
    }
    private func updateTimers() {
        if visible && refreshing {
            if progressTimer == nil {
                progressTimer = Timer.scheduledTimer(withTimeInterval: progressInterval, repeats: true) { [weak self] _ in self?.onProgress() }
                progressTimer?.tolerance = progressInterval * 0.2
            }
        } else { progressTimer?.invalidate(); progressTimer = nil }
        if visible {
            if clockTimer == nil {
                clockTimer = Timer.scheduledTimer(withTimeInterval: clockInterval, repeats: true) { [weak self] _ in self?.onClock() }
                clockTimer?.tolerance = clockInterval * 0.2
            }
        } else { clockTimer?.invalidate(); clockTimer = nil }
    }
    deinit { progressTimer?.invalidate(); clockTimer?.invalidate() }
}

/// Atomic file replacement changes the inode even when timestamps and size match.
final class FileSnapshots {
    struct Snapshot {
        let changed: Bool
        let data: Data?
    }
    private struct Stamp: Equatable {
        let inode: UInt64
        let device: UInt64
        let size: UInt64
        let permissions: UInt64
        let modified: Date?
    }
    private var stamps: [String: Stamp] = [:]
    private var contents: [String: Data] = [:]
    private var visited: Set<String> = []
    private let readData: (URL) throws -> Data

    init(readData: @escaping (URL) throws -> Data = { try Data(contentsOf: $0) }) { self.readData = readData }
    func read(_ path: String) -> Snapshot {
        let attrs = try? FileManager.default.attributesOfItem(atPath: path)
        let stamp = attrs.map {
            Stamp(inode: ($0[.systemFileNumber] as? NSNumber)?.uint64Value ?? 0,
                  device: ($0[.systemNumber] as? NSNumber)?.uint64Value ?? 0,
                  size: ($0[.size] as? NSNumber)?.uint64Value ?? 0,
                  permissions: ($0[.posixPermissions] as? NSNumber)?.uint64Value ?? 0,
                  modified: $0[.modificationDate] as? Date)
        }
        if visited.contains(path), stamps[path] == stamp { return Snapshot(changed: false, data: contents[path]) }
        let data = stamp == nil ? nil : try? readData(URL(fileURLWithPath: path))
        let changed = !visited.contains(path) || contents[path] != data
        visited.insert(path)
        // A transient read error must retry even if file attributes stay unchanged.
        stamps[path] = stamp != nil && data == nil ? nil : stamp
        contents[path] = data
        return Snapshot(changed: changed, data: data)
    }
}

/// Wait for process completion without waking a worker ten times per second.
enum BoundedProcess {
    static func run(_ process: Process, timeout: TimeInterval) throws -> Bool {
        let finished = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in finished.signal() }
        try process.run()
        guard finished.wait(timeout: .now() + timeout) == .timedOut else { return true }
        if process.isRunning { process.terminate() }
        if finished.wait(timeout: .now() + 0.3) == .timedOut {
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            _ = finished.wait(timeout: .now() + 1)
        }
        return false
    }
}
