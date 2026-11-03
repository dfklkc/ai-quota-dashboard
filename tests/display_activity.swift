// Compiled with the production models and configuration state, using an isolated data directory.
var checks = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    precondition(condition(), message)
    checks += 1
}
func runLoop(_ seconds: TimeInterval) { RunLoop.main.run(until: Date().addingTimeInterval(seconds)) }
let root = URL(fileURLWithPath: QuotaPaths.root)
try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
func write(_ name: String, _ value: Any) throws {
    try JSONSerialization.data(withJSONObject: value).write(to: root.appendingPathComponent(name), options: .atomic)
}

var reads = 0
let snapshots = FileSnapshots { url in reads += 1; return try Data(contentsOf: url) }
let file = root.appendingPathComponent("snapshot.json")
try Data("first".utf8).write(to: file)
let originalDate = try FileManager.default.attributesOfItem(atPath: file.path)[.modificationDate]!
check(snapshots.read(file.path).changed, "First read must publish")
check(!snapshots.read(file.path).changed && reads == 1, "Unchanged file must not be read twice")
try Data("other".utf8).write(to: file, options: .atomic)
try FileManager.default.setAttributes([.modificationDate: originalDate], ofItemAtPath: file.path)
check(snapshots.read(file.path).data == Data("other".utf8) && reads == 2, "Atomic replacement with equal size and mtime must be read")
try FileManager.default.removeItem(at: file)
let missing = snapshots.read(file.path)
check(missing.changed && missing.data == nil, "Deleted cache must clear previous data")
check(!snapshots.read(file.path).changed, "Repeated absence must not trigger republishing")
try Data("again".utf8).write(to: file)
check(snapshots.read(file.path).changed && reads == 3, "Recreated cache must be detected")

var attempts = 0
let transient = FileSnapshots { url in
    attempts += 1
    if attempts == 1 { throw NSError(domain: "ReadFixture", code: 1) }
    return try Data(contentsOf: url)
}
let failedRead = transient.read(file.path)
check(failedRead.changed && failedRead.data == nil, "Initial read failure must not expose old content")
let recoveredRead = transient.read(file.path)
check(recoveredRead.changed && recoveredRead.data == Data("again".utf8), "Transient read failure must recover with identical attributes")
check(!transient.read(file.path).changed && attempts == 2, "Recovered file should return to normal read deduplication")

var progress = 0, clocks = 0
let activity = DisplayActivity(progressInterval: 0.02, clockInterval: 0.03,
                               onProgress: { progress += 1 }, onClock: { clocks += 1 })
activity.setVisible(true)
runLoop(0.15)
check(progress == 0 && clocks > 1, "Idle card must have no progress polling")
activity.setRefreshing(true)
runLoop(0.15)
check(progress > 0, "Visible query should publish progress")
activity.setRefreshing(false)
let completedProgress = progress
runLoop(0.10)
check(progress == completedProgress, "Completed queries must stop progress polling")
activity.setVisible(false)
let hiddenClocks = clocks
activity.setRefreshing(true)
runLoop(0.10)
check(progress == completedProgress && clocks == hiddenClocks, "Hidden card must perform no UI timer work")
activity.setVisible(true)
runLoop(0.15)
check(progress > completedProgress && clocks > hiddenClocks, "Showing card must resume active progress and time")
activity.setRefreshing(false)
activity.setVisible(false)

let stamp = ISO8601DateFormatter().string(from: Date())
let agy: [String: Any] = ["ok": true, "is_running": true, "gemini_weekly_rem": 50, "claude_weekly_rem": 60, "last_success_at": stamp]
func configuration(gpt: Bool = false, agy: Bool = false, relay: Bool = false) -> [String: Any] {
    ["chatgpt_accounts": gpt ? [["id": "gpt_app", "name": "App", "type": "codex_auth", "enabled": true, "follow_current_session": true]] : [],
     "antigravity": ["enabled": agy], "linksapi": ["enabled": relay, "visible": relay]]
}
try write("ai_accounts_config.json", configuration(agy: true))
try write("antigravity_quota.json", agy)
let vm = CardViewModel()
vm.loadCaches()
check(vm.healthy && vm.attention == "各来源额度已同步", "AGY-only configuration must be healthy")
var changes = 0
let observation = vm.objectWillChange.sink { _ in changes += 1 }
vm.loadCaches(); vm.loadCaches()
check(changes == 0, "Unchanged caches must not republish the whole card")
try FileManager.default.removeItem(at: root.appendingPathComponent("antigravity_quota.json"))
vm.loadCaches()
check(vm.agy == nil && !vm.healthy, "Removed quota cache must not keep a stale amount")
try write("ai_accounts_config.json", configuration(relay: true))
try write("linksapi_quota.json", ["ok": true, "remaining_amount": 12.5, "amount_unit": "站内 USD", "last_success_at": stamp])
vm.loadCaches()
check(vm.healthy, "Relay-only configuration must be healthy")
try Data("broken".utf8).write(to: root.appendingPathComponent("linksapi_quota.json"), options: .atomic)
vm.loadCaches()
check(vm.linksapi == nil && !vm.healthy, "Corrupt relay cache must not keep stale balance")
try write("ai_accounts_config.json", configuration(gpt: true))
try write("gpt_accounts_quota.json", [["id": "gpt_app", "ok": true, "p_rem": 50, "s_rem": 70, "last_success_at": stamp]])
vm.loadCaches()
check(vm.healthy, "GPT-only configuration must be healthy")
try write("ai_accounts_config.json", configuration())
vm.refresh()
check(!vm.isRefreshing && vm.refreshMessage.contains("已暂停查询"), "Disabled sources must not start a query process")
check(vm.attention == "未启用任何来源", "All-disabled state must be explicit and neutral")

let configURL = root.appendingPathComponent("ai_accounts_config.json")
let corrupt = Data("broken configuration".utf8)
try corrupt.write(to: configURL, options: .atomic)
vm.loadCaches()
check(!vm.cacheError.isEmpty, "Unreadable configuration must be reported")
let invalid = ConfigState()
invalid.load(); invalid.save()
let preservedInvalid = try Data(contentsOf: configURL)
check(!invalid.isSuccess && preservedInvalid == corrupt, "Unreadable config must never be overwritten with defaults")
try write("ai_accounts_config.json", configuration())
let staleEditor = ConfigState(); staleEditor.load()
try corrupt.write(to: configURL, options: .atomic)
staleEditor.save()
let preservedStale = try Data(contentsOf: configURL)
check(!staleEditor.isSuccess && preservedStale == corrupt, "Configuration broken after opening settings must be preserved")
try write("ai_accounts_config.json", configuration())
try FileManager.default.createDirectory(at: root.appendingPathComponent("tokens"), withIntermediateDirectories: true)
try write("tokens/linksapi.json", ["user_id": "123", "access_token": "fixture-only"])
let editor = ConfigState(); editor.load(); editor.relayUserId = ""; editor.save()
let saved = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent("tokens/linksapi.json"))) as! [String: Any]
check(editor.isSuccess && saved["user_id"] as? String == "", "Clearing the user ID must actually clear it")
check(saved["access_token"] as? String == "fixture-only", "Clearing user ID must preserve existing credential")
var extraConfig = configuration(gpt: true)
var extraAccount = (extraConfig["chatgpt_accounts"] as! [[String: Any]])[0]
extraAccount["future_setting"] = "preserve-me"
extraConfig["chatgpt_accounts"] = [extraAccount]
try write("ai_accounts_config.json", extraConfig)
let compatibleEditor = ConfigState(); compatibleEditor.load(); compatibleEditor.save()
let compatibleSaved = try JSONSerialization.jsonObject(with: Data(contentsOf: configURL)) as! [String: Any]
check(compatibleEditor.isSuccess && (compatibleSaved["chatgpt_accounts"] as! [[String: Any]])[0]["future_setting"] as? String == "preserve-me", "Saving displayed settings must preserve unknown account options")
for value in [Double.nan, .infinity, -.infinity, 1e100] {
    check(resetLabel(value, now: Date()) == "未提供重置时间", "Invalid timestamps must not crash the display")
    check(windowTitle(value, fallback: "unknown") == "unknown", "Invalid window durations must not crash the display")
}
try write("ai_accounts_config.json", configuration(relay: true))
try write("linksapi_quota.json", ["ok": true, "used_amount": 1.25, "amount_unit": "站内 USD", "last_success_at": stamp,
                               "provider_name": "Fixture relay", "provider_app": "claude", "source_label": "CC Switch 当前提供商",
                               "analysis": ["available": true, "source": "站点当前Key日志", "sample_count": 2,
                                            "today": ["requests": 1, "tokens": 100, "amount": 0.1],
                                            "week": ["requests": 2, "tokens": 200, "amount": 0.2]]])
vm.loadCaches()
check(vm.healthy && !vm.attention.contains("余额待配置"), "Valid Key usage is healthy even when no wallet balance is returned")
check(vm.linksapi?.provider_name == "Fixture relay" && vm.linksapi?.analysis?.today?.tokens == 100, "CC Switch source and analysis must decode without affecting quota")
check(relaySiteURL("file:///tmp/example") == nil && relaySiteURL("https://user:pass@relay.example.com") == nil, "Dashboard links must not open unsafe URLs")
check(relaySiteURL("https://relay.example.com/dashboard/overview") != nil, "Validated relay dashboard should be openable")
var delayedRelay = vm.linksapi!
delayedRelay.last_success_at = ISO8601DateFormatter().string(from: Date().addingTimeInterval(-300))
check(relayFresh(delayedRelay, now: Date()), "CC Switch cached usage must remain valid until its scheduled refresh")
delayedRelay.last_success_at = ISO8601DateFormatter().string(from: Date().addingTimeInterval(-421))
check(!relayFresh(delayedRelay, now: Date()), "Old CC Switch usage must expire after the bounded freshness window")
let normal = Process(); normal.executableURL = URL(fileURLWithPath: "/usr/bin/true")
let normalFinished = try BoundedProcess.run(normal, timeout: 2)
check(normalFinished && normal.terminationStatus == 0, "Process completion should return without polling")
let hung = Process(); hung.executableURL = URL(fileURLWithPath: "/bin/sleep"); hung.arguments = ["5"]
let began = Date()
let hungFinished = try BoundedProcess.run(hung, timeout: 0.05)
check(!hungFinished && !hung.isRunning && Date().timeIntervalSince(began) < 2, "Hung process must terminate and release the worker")
observation.cancel()
print("PASS: \(checks) display activity, cache, source health and settings checks")
