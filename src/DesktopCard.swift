import Cocoa
import SwiftUI
import Darwin
import UserNotifications

struct GPTAccountQuota: Codable, Identifiable {
    var id: String
    var name: String?
    var email: String?
    var plan: String?
    var ok: Bool?
    var connected: Bool?
    var status_msg: String?
    var p_rem: Int?
    var p_used: Int?
    var p_reset_at: Double?
    var s_rem: Int?
    var s_used: Int?
    var s_reset_at: Double?
    var p_window_mins: Double?
    var s_window_mins: Double?
    var free_resets: Int?
    var source: String?
    var updated_at: String?
    var last_success_at: String?
    var token_sync_msg: String?
    var source_label: String?
    var detected_accounts: Int?
    var next_retry_at: Double?
}

struct AntigravityQuota: Codable {
    var ok: Bool?
    var is_running: Bool?
    var status_msg: String?
    var gemini_5h_rem: Int?
    var gemini_5h_used: Int?
    var gemini_5h_reset_at: Double?
    var gemini_weekly_rem: Int?
    var gemini_weekly_used: Int?
    var gemini_weekly_reset_at: Double?
    var claude_5h_rem: Int?
    var claude_5h_used: Int?
    var claude_5h_reset_at: Double?
    var claude_weekly_rem: Int?
    var claude_weekly_used: Int?
    var claude_weekly_reset_at: Double?
    var updated_at: String?
    var last_success_at: String?
    var next_retry_at: Double?
}

struct LinksAPIQuota: Codable {
    var ok: Bool?
    var state: String?
    var status_msg: String?
    var scope: String?
    var token_name: String?
    var unlimited: Bool?
    var remaining_amount: Double?
    var used_amount: Double?
    var granted_amount: Double?
    var amount_unit: String?
    var expires_at: Double?
    var claude_allowed: Bool?
    var last_success_at: String?
}
func relayAmount(_ value: Double?, unit: String?) -> String {
    guard let value = value else { return "未提供" }
    return String(format: "%.2f", value) + " " + (unit ?? "额度单位")
}

struct AccountConfigItem: Identifiable, Equatable {
    var id: String
    var name: String
    var email: String
    var enabled: Bool
    var type: String
    var auth_file: String
    var auto_sync_chrome: Bool = false
    var follow_current_session: Bool = false
}

func syncDate(_ raw: String?) -> Date? {
    guard let raw = raw else { return nil }
    return ISO8601DateFormatter().date(from: raw)
}
func fresh(_ raw: String?, now: Date) -> Bool {
    guard let d = syncDate(raw) else { return false }
    return now.timeIntervalSince(d) < 180 && now.timeIntervalSince(d) >= -60
}
func syncLabel(_ raw: String?) -> String {
    guard let d = syncDate(raw) else { return "尚无有效同步记录" }
    let f = DateFormatter()
    f.dateFormat = Calendar.current.isDateInToday(d) ? "HH:mm:ss" : "MM-dd HH:mm"
    return "同步于 " + f.string(from: d)
}
func resetLabel(_ ts: Double?, now: Date) -> String {
    guard let ts = ts else { return "未提供重置时间" }
    let seconds = Int(ts - now.timeIntervalSince1970)
    if seconds <= 0 { return "已到重置时间，等待同步" }
    let f = DateFormatter()
    let date = Date(timeIntervalSince1970: ts)
    f.dateFormat = Calendar.current.isDateInToday(date) ? "今日 HH:mm" : "MM-dd HH:mm"
    let remain = seconds >= 86400 ? "\(seconds / 86400)天\(seconds % 86400 / 3600)时" : seconds >= 3600 ? "\(seconds / 3600)时\(seconds % 3600 / 60)分" : seconds >= 60 ? "\(seconds / 60)分" : "不足1分"
    return "\(f.string(from: date)) · 余\(remain)"
}
func windowTitle(_ mins: Double?, fallback: String) -> String {
    guard let mins = mins, mins > 0 else { return fallback }
    if mins >= 1440 { return "\(Int(mins / 1440))天额度" }
    if mins >= 60 { return "\(Int(mins / 60))小时额度" }
    return "\(Int(mins))分钟额度"
}

class CardViewModel: ObservableObject {
    @Published var gptAccounts: [GPTAccountQuota] = []
    @Published var agy: AntigravityQuota?
    @Published var linksapi: LinksAPIQuota?
    @Published var relayVisible = true
    @Published var relayEnabled = false
    @Published var relayThreshold = 5.0
    @Published var relayName = "Claude · LinksAPI"
    @Published var agyEnabled = true
    @Published var customGeminiName = "Antigravity · Gemini"
    @Published var customClaudeName = "Antigravity · Claude"
    @Published var isRefreshing = false
    @Published var refreshMessage = "等待同步"
    @Published var isTopmost = UserDefaults.standard.object(forKey: "topmost") as? Bool ?? true
    @Published var compact = UserDefaults.standard.bool(forKey: "compact")
    @Published var now = Date()
    @Published var cacheError = ""
    private var fetching = false
    private let lock = NSLock()
    let root = QuotaPaths.root

    var lowQuotas: [(String, String)] {
        let threshold = UserDefaults.standard.object(forKey: "alertThreshold") as? Int ?? 20
        var values: [(String, String)] = []
        func add(_ name: String, _ pct: Int?, _ key: String, _ reset: Double?) {
            if let pct = pct, pct <= threshold {
                values.append((key + ":" + String(reset ?? 0) + ":" + String(threshold), "\(name)剩余 \(pct)%"))
            }
        }
        for a in gptAccounts where a.ok == true && fresh(a.last_success_at, now: now) {
            let prefix = (a.email ?? a.id)
            let name = a.id.contains("web") ? "GPT 网页" : "GPT App"
            add(name + "短周期", a.p_rem, prefix + ":primary", a.p_reset_at)
            add(name + "长周期", a.s_rem, prefix + ":secondary", a.s_reset_at)
        }
        if agyEnabled, let a = agy, a.ok == true && fresh(a.last_success_at, now: now) {
            add("Gemini 短周期", a.gemini_5h_rem, "gemini5h", a.gemini_5h_reset_at)
            add("Gemini 周额度", a.gemini_weekly_rem, "geminiWeek", a.gemini_weekly_reset_at)
            add("Claude 短周期", a.claude_5h_rem, "claude5h", a.claude_5h_reset_at)
            add("Claude 周额度", a.claude_weekly_rem, "claudeWeek", a.claude_weekly_reset_at)
        }
        if relayEnabled, let r = linksapi, r.ok == true, fresh(r.last_success_at, now: now),
           r.amount_unit == "站内 USD", let balance = r.remaining_amount, balance <= relayThreshold {
            values.append(("linksapi:" + (r.token_name ?? "") + ":" + String(r.granted_amount ?? 0) + ":" + String(relayThreshold), "LinksAPI 剩余 " + relayAmount(balance, unit: r.amount_unit)))
        }
        return values
    }
    var attention: String {
        if !cacheError.isEmpty { return cacheError }
        var messages = lowQuotas.map { $0.1 }
        let failed = gptAccounts.filter { $0.ok != true || !fresh($0.last_success_at, now: now) }.count
        if failed > 0 { messages.append("\(failed) 个账号待连接") }
        if agyEnabled && (agy?.ok != true || !fresh(agy?.last_success_at, now: now)) { messages.append("Antigravity 待同步") }
        else if agyEnabled, let a = agy {
            if a.gemini_5h_rem == nil && a.gemini_weekly_rem == nil { messages.append("Gemini 未提供额度") }
            if a.claude_5h_rem == nil && a.claude_weekly_rem == nil { messages.append("Claude 未提供额度") }
        }
        if relayEnabled && (linksapi?.ok != true || !fresh(linksapi?.last_success_at, now: now)) { messages.append("LinksAPI 待连接") }
        else if relayEnabled && linksapi?.remaining_amount == nil { messages.append("LinksAPI 账户余额待配置") }
        return messages.isEmpty ? "各来源额度已同步" : messages.joined(separator: " · ")
    }
    func notifyLowQuotas() {
        guard UserDefaults.standard.bool(forKey: "quotaNotifications") else { return }
        let center = UNUserNotificationCenter.current()
        var sent = UserDefaults.standard.stringArray(forKey: "sentQuotaAlerts") ?? []
        for (key, message) in lowQuotas where !sent.contains(key) {
            let content = UNMutableNotificationContent()
            content.title = "AI 额度提醒"; content.body = message
            center.add(UNNotificationRequest(identifier: key, content: content, trigger: nil))
            sent.append(key)
        }
        UserDefaults.standard.set(Array(sent.suffix(200)), forKey: "sentQuotaAlerts")
    }
    var healthy: Bool {
        cacheError.isEmpty && (!relayEnabled || (linksapi?.ok == true && linksapi?.remaining_amount != nil && fresh(linksapi?.last_success_at, now: now))) && !gptAccounts.isEmpty && gptAccounts.allSatisfy { $0.ok == true && fresh($0.last_success_at, now: now) } && (!agyEnabled || (agy?.ok == true && fresh(agy?.last_success_at, now: now) && (agy?.gemini_5h_rem != nil || agy?.gemini_weekly_rem != nil) && (agy?.claude_5h_rem != nil || agy?.claude_weekly_rem != nil)))
    }
    func refresh(manual: Bool = false, source: String? = nil) {
        lock.lock()
        if fetching { lock.unlock(); return }
        fetching = true
        lock.unlock()
        isRefreshing = true
        refreshMessage = "正在查询各来源…"
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self = self else { return }
            let process = Process()
            process.executableURL = QuotaPaths.python
            process.environment = QuotaPaths.childEnvironment
            var args = ["-B", QuotaPaths.script("fetch_quotas.py")]
            if manual { args.append("--force") }
            if let source = source { args += ["--source", source] }
            process.arguments = args
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            var message = "本轮查询完成 · 每60秒更新"
            do {
                try process.run()
                let deadline = Date().addingTimeInterval(25)
                while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
                if process.isRunning {
                    process.terminate()
                    Thread.sleep(forTimeInterval: 0.3)
                    if process.isRunning { kill(process.processIdentifier, SIGKILL) }
                    message = "查询超时 · 可点击重试"
                } else if process.terminationStatus == 75 {
                    message = "另一轮查询进行中，稍后自动更新"
                } else if process.terminationStatus != 0 {
                    message = "查询失败 · 检查账号配置后重试"
                }
            } catch { message = "无法启动查询 · 请检查脚本" }
            DispatchQueue.main.async {
                self.loadCaches()
                self.notifyLowQuotas()
                self.now = Date()
                self.refreshMessage = message
                self.isRefreshing = false
                self.lock.lock(); self.fetching = false; self.lock.unlock()
            }
        }
    }
    func loadCaches() {
        now = Date()
        cacheError = ""
        let configURL = URL(fileURLWithPath: root + "/ai_accounts_config.json")
        let config = (try? Data(contentsOf: configURL)).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        if let cfg = config?["linksapi"] as? [String: Any] {
            relayVisible = cfg["visible"] as? Bool ?? true
            relayEnabled = cfg["enabled"] as? Bool ?? false
            relayName = cfg["name"] as? String ?? "Claude · LinksAPI"
            relayThreshold = cfg["low_balance_threshold"] as? Double ?? 5
        }
        if let data = try? Data(contentsOf: URL(fileURLWithPath: root + "/linksapi_quota.json")) {
            linksapi = try? JSONDecoder().decode(LinksAPIQuota.self, from: data)
        }
        if let cfg = config?["antigravity"] as? [String: Any] {
            agyEnabled = cfg["enabled"] as? Bool ?? true
            customGeminiName = cfg["name_gemini"] as? String ?? customGeminiName
            customClaudeName = cfg["name_claude"] as? String ?? customClaudeName
        }
        if let data = try? Data(contentsOf: URL(fileURLWithPath: root + "/gpt_accounts_quota.json")),
           let accounts = try? JSONDecoder().decode([GPTAccountQuota].self, from: data) {
            gptAccounts = accounts
        } else { gptAccounts = []; cacheError = "账号缓存无法读取，请刷新" }
        // Reflect enabled state and names immediately after a configuration change.
        if let configs = config?["chatgpt_accounts"] as? [[String: Any]] {
            gptAccounts = configs.filter { $0["enabled"] as? Bool ?? true }.compactMap { cfg in
                guard let id = cfg["id"] as? String else { return nil }
                var a = gptAccounts.first { $0.id == id } ?? GPTAccountQuota(id: id)
                a.name = cfg["name"] as? String
                if cfg["follow_current_session"] as? Bool != true { a.email = cfg["email"] as? String }
                return a
            }
        }
        if let data = try? Data(contentsOf: URL(fileURLWithPath: root + "/antigravity_quota.json")) {
            agy = try? JSONDecoder().decode(AntigravityQuota.self, from: data)
        } else { agy = nil }
    }
}

class DraggableWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}
class DraggableHostingView<T: View>: NSHostingView<T> {
    override func mouseDown(with event: NSEvent) {
        // Controls receive their own events. Only unused header space initiates dragging.
        let pt = convert(event.locationInWindow, from: nil)
        let fromTop = isFlipped ? pt.y : bounds.height - pt.y
        if fromTop < 48 && pt.x < 205 { window?.performDrag(with: event) }
        else { super.mouseDown(with: event) }
    }
}

struct QuotaBarRow: View {
    var title: String
    var remPct: Int?
    var resetAt: Double?
    var barColor: Color
    var valid: Bool
    var now: Date
    var pct: Int? { valid ? remPct.map { min(100, max(0, $0)) } : nil }
    var tint: Color { guard let p = pct else { return .gray }; return p <= 20 ? .red : p <= 50 ? .yellow : barColor }
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title).foregroundColor(.white.opacity(0.85))
                Spacer()
                Text(pct.map { "剩余 \($0)%" } ?? (valid ? "未提供" : "—"))
                    .fontWeight(.semibold).foregroundColor(tint).monospacedDigit()
            }.font(.system(size: 11))
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.10))
                    if let p = pct { Capsule().fill(tint).frame(width: geo.size.width * CGFloat(p) / 100) }
                }
            }.frame(height: 5)
            Text(pct != nil ? "重置 \(resetLabel(resetAt, now: now))" : valid ? "此来源未提供该窗口" : "暂无有效额度，请查看连接状态")
                .font(.system(size: 9.5)).foregroundColor(.gray)
        }
        .accessibilityElement(children: .combine)
    }
}

struct GPTAccountCardView: View {
    var acc: GPTAccountQuota
    var now: Date
    var onEdit: () -> Void
    var onRetry: () -> Void
    var valid: Bool { acc.ok == true && fresh(acc.last_success_at, now: now) }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: acc.id.contains("web") ? "globe" : "desktopcomputer").foregroundColor(.cyan)
                Text(acc.name ?? acc.id).fontWeight(.semibold).lineLimit(1)
                Spacer(minLength: 4)
                Text(valid ? (acc.plan?.capitalized ?? "已同步") : "需处理")
                    .font(.system(size: 10, weight: .medium)).foregroundColor(valid ? .green : .orange)
                Button(action: onEdit) { Image(systemName: "pencil").foregroundColor(.gray) }.buttonStyle(.plain).help("配置此账号")
            }.font(.system(size: 12))
            Text(acc.email?.isEmpty == false ? acc.email! : "等待识别登录账号").font(.system(size: 9.5)).foregroundColor(.gray).lineLimit(1)
            if let source = acc.source_label {
                Text(source + ((acc.detected_accounts ?? 0) > 1 ? " · 检测到多个账号，优先活动页" : "")).font(.system(size: 9)).foregroundColor(.gray)
            }
            if !valid {
                Text(acc.ok == true ? "数据已过期，等待刷新" : (acc.status_msg ?? "等待首次同步"))
                    .font(.system(size: 10)).foregroundColor(.orange).fixedSize(horizontal: false, vertical: true)
            }
            if valid {
            QuotaBarRow(title: windowTitle(acc.p_window_mins, fallback: "短周期额度"), remPct: acc.p_rem, resetAt: acc.p_reset_at, barColor: .green, valid: valid, now: now)
            QuotaBarRow(title: windowTitle(acc.s_window_mins, fallback: "长周期额度"), remPct: acc.s_rem, resetAt: acc.s_reset_at, barColor: .cyan, valid: valid, now: now)
            } else {
                HStack {
                    Button("重试", action: onRetry)
                    if acc.id.contains("web") {
                        Button("打开 ChatGPT") { NSWorkspace.shared.open(URL(string: "https://chatgpt.com/")!) }
                    }
                    Button("连接说明", action: onEdit)
                }.buttonStyle(.borderless).font(.system(size: 10))
                if let retry = acc.next_retry_at, retry > now.timeIntervalSince1970 {
                    Text("自动重试：" + resetLabel(retry, now: now)).font(.system(size: 9)).foregroundColor(.gray)
                }
            }
            HStack {
                Text(syncLabel(acc.last_success_at))
                Spacer()
                if valid, let n = acc.free_resets, n > 0 { Text("重置包 \(n)次").foregroundColor(.orange) }
            }.font(.system(size: 9)).foregroundColor(.gray)
        }.padding(11).background(Color.white.opacity(0.045)).cornerRadius(12)
    }
}

struct AntigravityCardView: View {
    var name: String
    var model: String
    var data: AntigravityQuota?
    var now: Date
    var onEdit: () -> Void
    var hasQuota: Bool { gemini ? (data?.gemini_5h_rem != nil || data?.gemini_weekly_rem != nil) : (data?.claude_5h_rem != nil || data?.claude_weekly_rem != nil) }
    var valid: Bool { hasQuota && data?.ok == true && fresh(data?.last_success_at, now: now) }
    var gemini: Bool { model == "gemini" }
    var tint: Color { gemini ? .purple : .orange }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: gemini ? "sparkles" : "brain").foregroundColor(tint)
                Text(name).fontWeight(.semibold).lineLimit(1)
                Spacer(minLength: 4)
                Text(valid ? "已同步" : (data?.is_running == true && !hasQuota ? "未提供额度" : "待同步")).font(.system(size: 10)).foregroundColor(valid ? .green : .orange)
                Button(action: onEdit) { Image(systemName: "pencil").foregroundColor(.gray) }.buttonStyle(.plain).help("配置模块")
            }.font(.system(size: 12))
            Text(gemini ? "Gemini Flash / Pro 共享额度" : "Claude / GPT-OSS 共享额度")
                .font(.system(size: 9.5)).foregroundColor(.gray)
            if !valid { Text(data?.is_running == true && !hasQuota ? "此模型未提供额度窗口" : data?.ok == true ? "数据已过期，等待刷新" : (data?.status_msg ?? "等待首次同步")).font(.system(size: 10)).foregroundColor(.orange) }
            // Render only real windows; the current service may expose weekly limits only.
            if (gemini ? data?.gemini_5h_rem : data?.claude_5h_rem) != nil {
                QuotaBarRow(title: "5小时额度", remPct: gemini ? data?.gemini_5h_rem : data?.claude_5h_rem,
                            resetAt: gemini ? data?.gemini_5h_reset_at : data?.claude_5h_reset_at,
                            barColor: tint, valid: valid, now: now)
            }
            QuotaBarRow(title: "7天额度", remPct: gemini ? data?.gemini_weekly_rem : data?.claude_weekly_rem,
                        resetAt: gemini ? data?.gemini_weekly_reset_at : data?.claude_weekly_reset_at,
                        barColor: tint, valid: valid, now: now)
            HStack {
                Text(syncLabel(data?.last_success_at))
                Spacer()
                if (gemini ? data?.gemini_5h_rem : data?.claude_5h_rem) == nil { Text("5小时窗口未提供") }
            }.font(.system(size: 9)).foregroundColor(.gray)
        }.padding(11).background(Color.white.opacity(0.045)).cornerRadius(12)
    }
}

struct LinksAPICardView: View {
    var name: String
    var data: LinksAPIQuota?
    var enabled: Bool
    var compact: Bool
    var threshold: Double
    var now: Date
    var onSettings: () -> Void
    var onRetry: () -> Void
    var valid: Bool { enabled && data?.ok == true && fresh(data?.last_success_at, now: now) }
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Image(systemName: "creditcard").foregroundColor(.mint)
                Text(name).fontWeight(.semibold).foregroundColor(.white)
                Spacer()
                Text(!enabled ? "待接入" : valid ? (data?.remaining_amount != nil ? "已同步" : "仅用量已同步") : "待连接").font(.system(size: 10)).foregroundColor(valid && data?.remaining_amount != nil ? .green : .orange)
                Button(action: onSettings) { Image(systemName: "slider.horizontal.3") }.buttonStyle(.plain).accessibilityLabel("中转站设置")
            }.font(.system(size: 12))
            if valid {
                HStack {
                    Text("剩余费用额度")
                    Spacer()
                    Text(relayAmount(data?.remaining_amount, unit: data?.amount_unit)).fontWeight(.semibold)
                        .foregroundColor((data?.remaining_amount ?? .infinity) <= threshold ? .orange : .mint)
                }.font(.system(size: compact ? 11 : 14))
                if data?.unlimited == true {
                    Text("当前 Key 未设限，无法从它取得账户剩余余额").foregroundColor(.orange)
                    Button("配置账户余额查询", action: onSettings).buttonStyle(.borderless)
                }
                Text("已用费用：" + relayAmount(data?.used_amount, unit: data?.amount_unit))
                if !compact {
                    Text(data?.scope ?? "API 令牌额度")
                    if data?.claude_allowed == false { Text("此令牌的模型限制未包含 Claude").foregroundColor(.orange) }
                    Text(syncLabel(data?.last_success_at))
                }
            } else {
                Text(!enabled ? "已准备 · 配置后通过网络持续查询费用余额" : data?.status_msg ?? "等待网络查询")
                if !compact {
                    HStack {
                        Button(enabled ? "重新查询" : "配置接入", action: enabled ? onRetry : onSettings)
                        Button("打开控制台") { NSWorkspace.shared.open(URL(string: "https://linksapi.cn/dashboard/overview")!) }
                    }.buttonStyle(.borderless)
                }
            }
        }.font(.system(size: 10)).foregroundColor(.gray).padding(11).background(Color.white.opacity(0.045)).cornerRadius(12)
    }
}

struct CompactQuotaRow: View {
    var name: String
    var detail: String
    var short: Int?
    var long: Int?
    var valid: Bool
    func value(_ p: Int?) -> String { valid ? p.map { "\($0)%" } ?? "未提供" : "—" }
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(name).fontWeight(.semibold)
                Spacer()
                Text("短 " + value(short) + "  /  周 " + value(long)).monospacedDigit()
                    .foregroundColor(valid && [short, long].compactMap { $0 }.contains(where: { $0 <= 20 }) ? .orange : .cyan)
            }.font(.system(size: 11))
            Text(detail).font(.system(size: 10)).foregroundColor(.gray).lineLimit(1)
        }.padding(10).background(Color.white.opacity(0.045)).cornerRadius(9)
    }
}
struct CardView: View {
    @ObservedObject var vm: CardViewModel
    var onToggleTopmost: () -> Void
    var onClose: () -> Void
    var onRefreshTrigger: () -> Void
    var onOpenConfig: () -> Void
    var onToggleCompact: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 7) {
                Image(systemName: "line.3.horizontal").foregroundColor(.gray).help("拖动标题区域移动看板")
                Circle().fill(vm.healthy ? Color.green : Color.orange).frame(width: 7, height: 7)
                Text("AI 综合额度看板").font(.system(size: 13, weight: .bold))
                Spacer(minLength: 2)
                Button(action: onToggleCompact) { Image(systemName: vm.compact ? "arrow.up.left.and.arrow.down.right" : "arrow.down.right.and.arrow.up.left") }.accessibilityLabel(vm.compact ? "展开看板" : "紧凑模式")
                Button(action: onOpenConfig) { Image(systemName: "slider.horizontal.3") }.accessibilityLabel("看板设置")
                Button(action: onToggleTopmost) { Image(systemName: vm.isTopmost ? "pin.fill" : "pin") }.accessibilityLabel(vm.isTopmost ? "取消悬浮" : "置顶悬浮")
                Button(action: onClose) { Image(systemName: "xmark") }.accessibilityLabel("隐藏看板")
            }.buttonStyle(.plain).foregroundColor(.white).font(.system(size: 12))
            Text(vm.attention).font(.system(size: 10, weight: .medium)).foregroundColor(vm.lowQuotas.isEmpty && vm.healthy ? .gray : .orange).lineLimit(2)
            ScrollView(.vertical, showsIndicators: true) {
                VStack(alignment: .leading, spacing: 9) {
                    Text("GPT · Codex 使用额度").font(.system(size: 11, weight: .semibold)).foregroundColor(.cyan)
                    ForEach(vm.gptAccounts) { acc in
                        if vm.compact {
                            CompactQuotaRow(name: acc.name ?? acc.id, detail: acc.ok == true ? (acc.email ?? "") : (acc.status_msg ?? "待连接"), short: acc.p_rem, long: acc.s_rem, valid: acc.ok == true && fresh(acc.last_success_at, now: vm.now))
                        } else {
                            GPTAccountCardView(acc: acc, now: vm.now, onEdit: onOpenConfig, onRetry: { vm.refresh(manual: true, source: acc.id) })
                        }
                    }
                    if vm.agyEnabled {
                        if vm.compact {
                            CompactQuotaRow(name: vm.customGeminiName, detail: vm.agy?.is_running == true && vm.agy?.gemini_5h_rem == nil && vm.agy?.gemini_weekly_rem == nil ? "此模型未提供额度窗口" : vm.agy?.status_msg ?? "待连接", short: vm.agy?.gemini_5h_rem, long: vm.agy?.gemini_weekly_rem, valid: vm.agy?.ok == true && fresh(vm.agy?.last_success_at, now: vm.now))
                            CompactQuotaRow(name: vm.customClaudeName, detail: vm.agy?.is_running == true && vm.agy?.claude_5h_rem == nil && vm.agy?.claude_weekly_rem == nil ? "此模型未提供额度窗口" : vm.agy?.status_msg ?? "待连接", short: vm.agy?.claude_5h_rem, long: vm.agy?.claude_weekly_rem, valid: vm.agy?.ok == true && fresh(vm.agy?.last_success_at, now: vm.now))
                        } else {
                            AntigravityCardView(name: vm.customGeminiName, model: "gemini", data: vm.agy, now: vm.now, onEdit: onOpenConfig)
                            AntigravityCardView(name: vm.customClaudeName, model: "claude", data: vm.agy, now: vm.now, onEdit: onOpenConfig)
                        }
                    }
                    if vm.relayVisible {
                        LinksAPICardView(name: vm.relayName, data: vm.linksapi, enabled: vm.relayEnabled, compact: vm.compact, threshold: vm.relayThreshold, now: vm.now, onSettings: onOpenConfig, onRetry: { vm.refresh(manual: true, source: "linksapi") })
                    }
                }
            }
            HStack {
                Text(vm.refreshMessage).font(.system(size: 9.5)).foregroundColor(.gray).lineLimit(1)
                Spacer(minLength: 4)
                Button(action: onRefreshTrigger) { Label(vm.isRefreshing ? "查询中…" : "刷新", systemImage: "arrow.clockwise").font(.system(size: 10)) }.buttonStyle(.borderless).disabled(vm.isRefreshing)
            }
        }.padding(14).frame(width: 400, height: vm.compact ? (vm.relayVisible ? 470 : 400) : 770)
            .foregroundColor(.white)
            .background(RoundedRectangle(cornerRadius: 18).fill(Color(white: 0.09).opacity(0.98)))
            .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color.white.opacity(0.14), lineWidth: 1))
            .preferredColorScheme(.dark)
    }
}

class ConfigState: ObservableObject {
    @Published var accounts: [AccountConfigItem] = []
    @Published var geminiName: String = "Antigravity · Gemini"
    @Published var claudeName: String = "Antigravity · Claude"
    @Published var agyEmail: String = ""
    @Published var relayEnabled = false
    @Published var relayVisible = true
    @Published var relayName = "Claude · LinksAPI"
    @Published var relayUseClaudeSettings = false
    @Published var relayMode = "account"
    @Published var relayKey = ""
    @Published var relayUserId = ""
    @Published var relayThreshold = 5.0
    @Published var agyEnabled: Bool = true
    
    @Published var tokenInputs: [String: String] = [:]
    @Published var expandedTokenAccounts: Set<String> = []
    
    @Published var isAddingNew: Bool = false
    @Published var newName: String = ""
    @Published var newId: String = ""
    @Published var newEmail: String = ""
    @Published var newType: String = "token_file"
    @Published var newToken: String = ""
    
    @Published var statusMessage: String? = nil
    @Published var isSuccess: Bool = false
    @Published var isBusy: Bool = false
    
    let configPath = QuotaPaths.root + "/ai_accounts_config.json"
    let cardConfigPath = QuotaPaths.root + "/card_config.json"
    let tokensDir = QuotaPaths.root + "/tokens"
    let bindScriptPath = QuotaPaths.script("bind_account.py")
    
    func load() {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: configPath)),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return
        }
        
        if let list = dict["chatgpt_accounts"] as? [[String: Any]] {
            var parsed: [AccountConfigItem] = []
            for d in list {
                let id = d["id"] as? String ?? ""
                let name = d["name"] as? String ?? ""
                let email = d["email"] as? String ?? ""
                let enabled = d["enabled"] as? Bool ?? true
                let type = d["type"] as? String ?? "token_file"
                let authFile = d["auth_file"] as? String ?? ""
                parsed.append(AccountConfigItem(id: id, name: name, email: email, enabled: enabled, type: type, auth_file: authFile, auto_sync_chrome: d["auto_sync_chrome"] as? Bool ?? false, follow_current_session: d["follow_current_session"] as? Bool ?? false))
            }
            self.accounts = parsed
        }
        
        if let relay = dict["linksapi"] as? [String: Any] {
            relayEnabled = relay["enabled"] as? Bool ?? false
            relayVisible = relay["visible"] as? Bool ?? true
            relayName = relay["name"] as? String ?? "Claude · LinksAPI"
            relayMode = relay["query_mode"] as? String ?? "account"
            relayUseClaudeSettings = relay["credential_source"] as? String == "claude_settings"
            relayThreshold = relay["low_balance_threshold"] as? Double ?? 5
        }
        let credentialURL = URL(fileURLWithPath: tokensDir + "/linksapi.json")
        if let data = try? Data(contentsOf: credentialURL),
           let saved = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            relayUserId = saved["user_id"] as? String ?? ""
        }
        if let agy = dict["antigravity"] as? [String: Any] {
            self.agyEnabled = agy["enabled"] as? Bool ?? true
            self.agyEmail = agy["email"] as? String ?? ""
            self.geminiName = agy["name_gemini"] as? String ?? "Antigravity · Gemini"
            self.claudeName = agy["name_claude"] as? String ?? "Antigravity · Claude"
        }
        
        if let cardData = try? Data(contentsOf: URL(fileURLWithPath: cardConfigPath)),
           let cardDict = try? JSONSerialization.jsonObject(with: cardData) as? [String: Any],
           let names = cardDict["names"] as? [String: String] {
            if let g = names["antigravity_gemini"], !g.isEmpty && self.geminiName.isEmpty { self.geminiName = g }
            if let c = names["antigravity_claude"], !c.isEmpty && self.claudeName.isEmpty { self.claudeName = c }
        }
    }
    
    func save(onSuccess: (() -> Void)? = nil) {
        do {
        var rootDict: [String: Any] = [:]
        if let data = try? Data(contentsOf: URL(fileURLWithPath: configPath)),
           let existing = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            rootDict = existing
        }
        
        var relayCfg = rootDict["linksapi"] as? [String: Any] ?? [:]
        relayCfg["enabled"] = relayEnabled; relayCfg["visible"] = relayVisible
        relayCfg["name"] = relayName; relayCfg["query_mode"] = relayUseClaudeSettings ? "token" : relayMode
        relayCfg["credential_source"] = relayUseClaudeSettings ? "claude_settings" : "manual"
        relayCfg["low_balance_threshold"] = relayThreshold
        rootDict["linksapi"] = relayCfg
        let credentialURL = URL(fileURLWithPath: tokensDir + "/linksapi.json")
        var credentials: [String: Any] = [:]
        if let data = try? Data(contentsOf: credentialURL), let saved = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { credentials = saved }
        let cleanKey = relayKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cleanKey.isEmpty || (!relayUserId.isEmpty && relayUserId != credentials["user_id"] as? String) {
            if cleanKey.contains(where: { $0.isWhitespace }) { throw NSError(domain: "LinksAPI", code: 1, userInfo: [NSLocalizedDescriptionKey: "查询凭证含有空格或换行，请重新粘贴"]) }
            if !cleanKey.isEmpty { credentials[relayMode == "account" ? "access_token" : "api_key"] = cleanKey }
            credentials["user_id"] = relayUserId.trimmingCharacters(in: .whitespacesAndNewlines)
            try FileManager.default.createDirectory(atPath: tokensDir, withIntermediateDirectories: true)
            let temp = URL(fileURLWithPath: tokensDir + "/.linksapi-" + UUID().uuidString)
            let data = try JSONSerialization.data(withJSONObject: credentials)
            guard FileManager.default.createFile(atPath: temp.path, contents: data, attributes: [.posixPermissions: 0o600]) else { throw NSError(domain: "LinksAPI", code: 2, userInfo: [NSLocalizedDescriptionKey: "凭证保存失败"]) }
            defer { try? FileManager.default.removeItem(at: temp) }
            guard rename(temp.path, credentialURL.path) == 0 else { throw NSError(domain: "LinksAPI", code: 3, userInfo: [NSLocalizedDescriptionKey: "凭证文件替换失败"]) }
            relayKey = ""
        }
        var accList: [[String: Any]] = []
        for acc in accounts {
            let item: [String: Any] = [
                "id": acc.id,
                "name": acc.name,
                "email": acc.email,
                "enabled": acc.enabled,
                "type": acc.type,
                "auth_file": acc.auth_file,
                "auto_sync_chrome": acc.auto_sync_chrome,
                "follow_current_session": acc.follow_current_session
            ]
            accList.append(item)
            
            if acc.type == "token_file", !acc.follow_current_session, let token = tokenInputs[acc.id], !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                let expandedPath = NSString(string: acc.auth_file).expandingTildeInPath
                let dir = (expandedPath as NSString).deletingLastPathComponent
                try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true, attributes: nil)
                
                let tokenDict: [String: Any] = [
                    "access_token": token.trimmingCharacters(in: .whitespacesAndNewlines),
                    "email": acc.email
                ]
                let tdata = try JSONSerialization.data(withJSONObject: tokenDict, options: [.prettyPrinted])
                try tdata.write(to: URL(fileURLWithPath: expandedPath), options: .atomic)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: expandedPath)
            }
        }
        rootDict["chatgpt_accounts"] = accList
        
        var agyDict: [String: Any] = rootDict["antigravity"] as? [String: Any] ?? [:]
        agyDict["enabled"] = agyEnabled
        agyDict["email"] = agyEmail
        agyDict["name_gemini"] = geminiName
        agyDict["name_claude"] = claudeName
        rootDict["antigravity"] = agyDict
        
        let outData = try JSONSerialization.data(withJSONObject: rootDict, options: [.prettyPrinted])
        try outData.write(to: URL(fileURLWithPath: configPath), options: .atomic)
        
        var cardDict: [String: Any] = [:]
        if let cData = try? Data(contentsOf: URL(fileURLWithPath: cardConfigPath)),
           let existing = try? JSONSerialization.jsonObject(with: cData) as? [String: Any] {
            cardDict = existing
        }
        var namesDict = cardDict["names"] as? [String: String] ?? [:]
        namesDict["antigravity_gemini"] = geminiName
        namesDict["antigravity_claude"] = claudeName
        if let firstApp = accounts.first(where: { $0.type == "codex_auth" }) {
            namesDict["gpt_app"] = firstApp.name
        }
        if let firstWeb = accounts.first(where: { $0.type == "token_file" }) {
            namesDict["gpt_web"] = firstWeb.name
        }
        cardDict["names"] = namesDict
        let cardOut = try JSONSerialization.data(withJSONObject: cardDict, options: [.prettyPrinted])
        try cardOut.write(to: URL(fileURLWithPath: cardConfigPath), options: .atomic)
        
        isSuccess = true
        statusMessage = "✅ 配置与名称已保存！看板已同步更新。"
        onSuccess?()
        } catch {
            isSuccess = false
            statusMessage = "保存未完成，请检查配置文件权限后重试：\(error.localizedDescription)"
        }
    }
    
    func bindFromChrome(accountId: String, onSuccess: (() -> Void)? = nil) {
        guard !isBusy else { return }
        isBusy = true
        isSuccess = false
        statusMessage = "正在读取 Chrome 当前登录账号，并验证额度…"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let proc = Process()
            proc.executableURL = QuotaPaths.python
            proc.environment = QuotaPaths.childEnvironment
            proc.arguments = ["-B", self.bindScriptPath, accountId]
            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = FileHandle.nullDevice
            do {
                try proc.run()
                let deadline = Date().addingTimeInterval(20)
                while proc.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
                if proc.isRunning {
                    proc.terminate()
                    Thread.sleep(forTimeInterval: 0.2)
                    if proc.isRunning { kill(proc.processIdentifier, SIGKILL) }
                    DispatchQueue.main.async {
                        self.isBusy = false
                        self.statusMessage = "读取 Chrome 超时，请重新加载 ChatGPT 页面后重试"
                    }
                    return
                }
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                let response = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                DispatchQueue.main.async {
                    self.isBusy = false
                    self.isSuccess = proc.terminationStatus == 0 && response?["ok"] as? Bool == true
                    self.statusMessage = response?["message"] as? String ?? "凭证同步失败，请检查浏览器权限或重新加载页面"
                    if self.isSuccess { onSuccess?() }
                }
            } catch {
                DispatchQueue.main.async {
                    self.isBusy = false
                    self.statusMessage = "无法执行凭证同步，请检查绑定程序"
                }
            }
        }
    }

    func addNewAccount() {
        let cleanName = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        var cleanId = newId.trimmingCharacters(in: .whitespacesAndNewlines)
        if cleanName.isEmpty {
            statusMessage = "⚠️ 请输入账号名称"
            isSuccess = false
            return
        }
        if cleanId.isEmpty {
            cleanId = "gpt_" + String(Int(Date().timeIntervalSince1970))
        }
        
        if accounts.contains(where: { $0.id == cleanId }) {
            statusMessage = "⚠️ 账号 ID [\(cleanId)] 已存在，请更换"
            isSuccess = false
            return
        }
        
        let authPath: String
        if newType == "token_file" {
            authPath = "~/.swiftbar/tokens/\(cleanId).json"
        } else {
            authPath = "~/.codex/auth.json"
        }
        
        let item = AccountConfigItem(
            id: cleanId,
            name: cleanName,
            email: newEmail.trimmingCharacters(in: .whitespacesAndNewlines),
            enabled: true,
            type: newType,
            auth_file: authPath,
            auto_sync_chrome: newType == "token_file"
        )
        
        if !newToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            tokenInputs[cleanId] = newToken.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        
        accounts.append(item)
        
        newName = ""
        newId = ""
        newEmail = ""
        newToken = ""
        isAddingNew = false
        isSuccess = true
        statusMessage = "✅ 账号已添加！请点击下方「保存并立即刷新」生效。"
    }
    
    func deleteAccount(at index: Int) {
        guard index >= 0 && index < accounts.count else { return }
        let removed = accounts.remove(at: index)
        statusMessage = "已移除账号: \(removed.name)，点击「保存」生效"
        isSuccess = false
    }
}

// MARK: - Dedicated Frontend Config Root View
struct ConfigRootView: View {
    @StateObject var state = ConfigState()
    @AppStorage("alertThreshold") var threshold = 20
    @AppStorage("quotaNotifications") var notifications = false
    @State var advanced = false
    @State var notificationMessage = ""
    var onSaved: () -> Void
    var onClose: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            Text("看板设置").font(.system(size: 18, weight: .bold))
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("GPT 跟随当前登录账号 · 显示 Codex 使用额度").font(.headline)
                    ForEach(state.accounts.indices, id: \.self) { i in
                        VStack(alignment: .leading, spacing: 8) {
                            Toggle(state.accounts[i].type == "token_file" ? "Chrome 网页来源" : "App 来源", isOn: $state.accounts[i].enabled)
                            TextField("显示名称", text: $state.accounts[i].name).textFieldStyle(.roundedBorder)
                            if state.accounts[i].type == "token_file" {
                                Text("优先前台窗口的活动 ChatGPT 标签页，其次读取其他已登录页面。多账号时卡片标注来源。").font(.caption).foregroundColor(.secondary)
                                Button("从 Chrome 读取当前账号") { state.bindFromChrome(accountId: state.accounts[i].id, onSuccess: onSaved) }.disabled(state.isBusy)
                            } else {
                                Text("读取 App 本地登录，切换账号后在下次刷新更新。").font(.caption).foregroundColor(.secondary)
                            }
                        }.padding(12).background(Color.white.opacity(0.05)).cornerRadius(10)
                    }
                    Divider()
                    VStack(alignment: .leading, spacing: 9) {
                        Text("Claude 中转站 · LinksAPI").font(.headline)
                        Toggle("在看板显示中转站", isOn: $state.relayVisible)
                        Toggle("启用网络费用查询", isOn: $state.relayEnabled)
                        TextField("卡片名称", text: $state.relayName).textFieldStyle(.roundedBorder)
                        Toggle("跟随 Claude 当前的 LinksAPI 配置", isOn: $state.relayUseClaudeSettings)
                            .onChange(of: state.relayUseClaudeSettings) { _, follow in if follow { state.relayMode = "token" } }
                        Picker("查询范围", selection: $state.relayMode) {
                            Text("账户钱包余额").tag("account")
                            Text("API Key 独立额度").tag("token")
                        }
                        .disabled(state.relayUseClaudeSettings)
                        if state.relayUseClaudeSettings {
                            Text("直接读取 Claude 本机配置中的 Key，不复制保存；切换到其他服务商后停止查询 LinksAPI。").font(.caption).foregroundColor(.secondary)
                        } else {
                        SecureField(state.relayMode == "account" ? "账户访问令牌（留空保留已有值）" : "API Key（留空保留已有值）", text: $state.relayKey).textFieldStyle(.roundedBorder)
                        }
                        if state.relayMode == "account" {
                            TextField("用户 ID", text: $state.relayUserId).textFieldStyle(.roundedBorder)
                            Text("账户钱包模式需站点用户级访问令牌（Access Token）及用户 ID。凭证可在站点个人设置中查看；它与 Claude 的模型 API Key 不同。余额可能由多个模型共享。").font(.caption).foregroundColor(.secondary)
                        } else {
                            Text("此模式显示单个 API Key 的费用额度。未设上限的 Key 不能据此得到账户钱包余额。").font(.caption).foregroundColor(.secondary)
                        }
                        Stepper("余额提醒：低于 \(Int(state.relayThreshold)) 站内 USD", value: $state.relayThreshold, in: 0...100, step: 1)
                        Text("每 60 秒直接请求 LinksAPI，不依赖网页打开；凭证仅保存在本机专用文件。").font(.caption).foregroundColor(.secondary)
                        Button("打开 LinksAPI 控制台") { NSWorkspace.shared.open(URL(string: "https://linksapi.cn/dashboard/overview")!) }
                    }.padding(12).background(Color.white.opacity(0.05)).cornerRadius(10)
                    Divider()
                    Toggle("启用 Antigravity", isOn: $state.agyEnabled)
                    TextField("Gemini 标题", text: $state.geminiName).textFieldStyle(.roundedBorder)
                    TextField("Claude 标题", text: $state.claudeName).textFieldStyle(.roundedBorder)
                    Divider()
                    Stepper("低额度提醒阈值：\(threshold)%", value: $threshold, in: 5...50, step: 5)
                    Toggle("发送系统通知（同一账号和额度周期只提醒一次）", isOn: $notifications)
                        .onChange(of: notifications) { _, enabled in
                            if enabled {
                                UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, _ in
                                    DispatchQueue.main.async {
                                        notificationMessage = granted ? "系统通知已开启" : "通知未授权，可在系统设置中开启"
                                        if !granted { notifications = false }
                                    }
                                }
                            }
                        }
                    if !notificationMessage.isEmpty { Text(notificationMessage).font(.caption).foregroundColor(.secondary) }
                    DisclosureGroup("连接说明与高级设置", isExpanded: $advanced) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Chrome 需保留已登录的 chatgpt.com 页面；脚本读取依赖现有 Apple 事件 JavaScript 和 macOS 自动化权限。失败时先重新加载页面，再点击重试。")
                            Text("每 60 秒检查；连续失败后按 60、120、240、300 秒重试。手动刷新立即重试。")
                            Text("关闭看板仅隐藏窗口；菜单栏可再次打开或退出。置顶与紧凑模式会保存。")
                            Button("打开账号配置文件") { NSWorkspace.shared.open(URL(fileURLWithPath: state.configPath)) }
                        }.font(.caption).foregroundColor(.secondary).padding(.top, 8)
                    }
                }
            }
            if let message = state.statusMessage { Text(message).font(.caption).foregroundColor(state.isSuccess ? .green : .orange).fixedSize(horizontal: false, vertical: true) }
            HStack {
                Button("关闭", action: onClose)
                Spacer()
                Button("保存并刷新") { state.save { onSaved(); onClose() } }.buttonStyle(.borderedProminent).disabled(state.isBusy)
            }
        }.padding(20).frame(width: 540, height: 700).preferredColorScheme(.dark)
            .onAppear { state.load() }
    }
}

// MARK: - App Delegate
class AppDelegate: NSObject, NSApplicationDelegate {
    var window: DraggableWindow!
    var configWindow: NSWindow?
    var statusItem: NSStatusItem?
    var progressTimer: Timer?
    let vm = CardViewModel()
    let posPath = QuotaPaths.root + "/desktop_card_pos.json"
    
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        do { try QuotaPaths.prepareDefaults() }
        catch {
            let alert = NSAlert(); alert.messageText = "无法初始化额度看板"
            alert.informativeText = error.localizedDescription; alert.runModal(); NSApp.terminate(nil); return
        }
        
        let initialPos = loadSavedPosition()
        
        let cardView = CardView(
            vm: vm,
            onToggleTopmost: { [weak self] in
                self?.toggleTopmost()
            },
            onClose: {
                self.window.orderOut(nil)
            },
            onRefreshTrigger: { [weak self] in
                self?.vm.refresh(manual: true)
            },
            onOpenConfig: { [weak self] in
                self?.openConfigWindow()
            },
            onToggleCompact: { [weak self] in self?.toggleCompact() }
        )
        
        let hostingView = DraggableHostingView(rootView: cardView)
        
        window = DraggableWindow(
            contentRect: NSRect(x: initialPos.x, y: initialPos.y, width: 400, height: vm.compact ? (vm.relayVisible ? 470 : 400) : 770),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.title = "AI 综合额度看板"
        window.contentView = hostingView
        window.backgroundColor = .clear
        window.isOpaque = false
        window.hasShadow = true
        window.isMovable = true
        
        window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        window.level = vm.isTopmost ? .floating : .normal
        
        window.makeKeyAndOrderFront(nil)
        configureMenu()
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(didWake), name: NSWorkspace.didWakeNotification, object: nil)
        progressTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            if self.vm.isRefreshing { self.vm.loadCaches() }
        }
        vm.loadCaches()
        vm.refresh(manual: true)
        
        // Network requests run once per minute.
        Timer.scheduledTimer(withTimeInterval: 60.0, repeats: true) { [weak self] _ in
            self?.vm.refresh(manual: false)
            self?.savePosition()
        }
        Timer.scheduledTimer(withTimeInterval: 15.0, repeats: true) { [weak self] _ in
            self?.vm.now = Date()
        }
    }
    
    func openConfigWindow() {
        if configWindow == nil {
            let configView = ConfigRootView(
                onSaved: { [weak self] in
                    self?.vm.loadCaches()
                    self?.vm.refresh(manual: true)
                },
                onClose: { [weak self] in
                    self?.configWindow?.orderOut(nil)
                }
            )
            let hosting = NSHostingView(rootView: configView)
            let win = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 540, height: 700),
                styleMask: [.titled, .closable, .miniaturizable],
                backing: .buffered,
                defer: false
            )
            win.title = "AI 额度看板 · 设置"
            win.contentView = hosting
            win.center()
            win.isReleasedWhenClosed = false
            win.appearance = NSAppearance(named: .darkAqua)
            self.configWindow = win
        } else {
            let configView = ConfigRootView(
                onSaved: { [weak self] in
                    self?.vm.loadCaches()
                    self?.vm.refresh(manual: true)
                },
                onClose: { [weak self] in
                    self?.configWindow?.orderOut(nil)
                }
            )
            configWindow?.contentView = NSHostingView(rootView: configView)
        }
        
        configWindow?.level = NSWindow.Level(rawValue: NSWindow.Level.floating.rawValue + 1)
        configWindow?.center()
        configWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    
    func toggleTopmost() {
        vm.isTopmost.toggle()
        UserDefaults.standard.set(vm.isTopmost, forKey: "topmost")
        if vm.isTopmost {
            window.level = .floating
        } else {
            window.level = .normal
        }
    }
    
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }
    func configureMenu() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem?.button?.image = NSImage(systemSymbolName: "gauge.with.dots.needle.50percent", accessibilityDescription: "AI 额度看板")
        let menu = NSMenu()
        for (title, action) in [("显示／隐藏看板", #selector(toggleVisibility)), ("立即刷新", #selector(menuRefresh)), ("切换紧凑模式", #selector(toggleCompact)), ("看板设置", #selector(menuSettings)), ("退出", #selector(quit))] {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: ""); item.target = self; menu.addItem(item)
        }
        statusItem?.menu = menu
    }
    @objc func toggleVisibility() {
        if window.isVisible { window.orderOut(nil) }
        else { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    }
    @objc func menuRefresh() { vm.refresh(manual: true) }
    @objc func menuSettings() { openConfigWindow() }
    @objc func quit() { NSApp.terminate(nil) }
    @objc func didWake() { vm.loadCaches(); vm.refresh(manual: true) }
    @objc func toggleCompact() {
        let top = window.frame.maxY
        vm.compact.toggle()
        UserDefaults.standard.set(vm.compact, forKey: "compact")
        let height: CGFloat = vm.compact ? (vm.relayVisible ? 470 : 400) : 770
        window.setContentSize(NSSize(width: 400, height: height))
        let screen = window.screen?.visibleFrame ?? NSScreen.main!.visibleFrame
        window.setFrameOrigin(NSPoint(x: window.frame.minX, y: max(screen.minY, min(top - height, screen.maxY - height))))
        savePosition()
    }
    func loadSavedPosition() -> NSPoint {
        if let data = try? Data(contentsOf: URL(fileURLWithPath: posPath)),
           let dict = try? JSONSerialization.jsonObject(with: data) as? [String: CGFloat],
           let x = dict["x"], let y = dict["y"] {
            let size = NSSize(width: 400, height: vm.compact ? (vm.relayVisible ? 470 : 400) : 770)
            let rect = NSScreen.screens.map { $0.visibleFrame }.first { $0.contains(NSPoint(x: x + 100, y: y + 100)) } ?? NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
            return NSPoint(x: min(max(x, rect.minX), rect.maxX - size.width), y: min(max(y, rect.minY), rect.maxY - size.height))
        }
        if let screen = NSScreen.main {
            let cx = screen.visibleFrame.midX + 80
            let cy = max(screen.visibleFrame.minY, screen.visibleFrame.midY - 360)
            return NSPoint(x: cx, y: cy)
        }
        return NSPoint(x: 200, y: 300)
    }
    
    func savePosition() {
        let frame = window.frame
        let dict: [String: CGFloat] = ["x": frame.origin.x, "y": frame.origin.y]
        if let data = try? JSONSerialization.data(withJSONObject: dict) {
            try? data.write(to: URL(fileURLWithPath: posPath))
        }
    }
    
    func applicationWillTerminate(_ notification: Notification) {
        savePosition()
    }
}

if CommandLine.arguments.contains("--self-check") { exit(QuotaPaths.selfCheck()) }
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
