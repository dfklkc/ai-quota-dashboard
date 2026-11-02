import AppKit

_ = NSApplication.shared
let card = NSWindow(contentRect: .zero, styleMask: [.borderless], backing: .buffered, defer: false)
let settings = NSWindow(contentRect: .zero, styleMask: [.titled], backing: .buffered, defer: false)
var checks = 0
func check(_ condition: Bool, _ message: String) {
    precondition(condition, message)
    checks += 1
}

// These windows remain invisible: test real AppKit levels without manipulating the desktop.
WindowPresentation.apply(pinned: false, card: card)
check(card.level.rawValue < NSWindow.Level.normal.rawValue, "Unpinned card must stay below every normal app")
check(card.level.rawValue > Int(CGWindowLevelForKey(.desktopIconWindow)), "Card controls must remain above Finder icons")
check(card.collectionBehavior.contains(.stationary), "Desktop card must stay stationary")
check(card.collectionBehavior.contains(.canJoinAllSpaces), "Desktop card must remain available across Spaces")

// Opening settings after startup must obey the current mode.
WindowPresentation.apply(pinned: false, card: card, settings: settings)
check(settings.level == .normal, "Unpinned settings must not float over other apps")
WindowPresentation.apply(pinned: true, card: card, settings: settings)
check(card.level == .floating, "Pinning must raise the card")
check(settings.level.rawValue > card.level.rawValue, "Pinned settings must remain above the card")
check(!card.collectionBehavior.contains(.stationary), "Pinned card must use normal window movement")

// Repeated mode changes and reapplying on show/reopen cannot leave a stale floating window.
for _ in 0..<3 {
    WindowPresentation.apply(pinned: false, card: card, settings: settings)
    check(card.level == WindowPresentation.desktopLevel, "Unlock must restore desktop level")
    check(settings.level == .normal, "Unlock must also lower an existing settings window")
    WindowPresentation.apply(pinned: true, card: card, settings: settings)
    check(card.level == .floating, "Pinning again must restore floating level")
}
WindowPresentation.apply(pinned: false, card: card, settings: settings)
check(!card.isVisible && !settings.isVisible, "Applying policy must not show hidden windows")
check(!card.isKeyWindow && !settings.isKeyWindow, "Applying policy must not steal keyboard focus")
print("PASS: \(checks) AppKit window presentation checks")
