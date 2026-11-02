import AppKit

/// Unpinned cards belong to the desktop, even when the app is activated.
enum WindowPresentation {
    static let desktopLevel = NSWindow.Level(
        rawValue: Int(CGWindowLevelForKey(.desktopIconWindow)) + 1
    )

    static func apply(pinned: Bool, card: NSWindow, settings: NSWindow? = nil) {
        card.level = pinned ? .floating : desktopLevel
        card.collectionBehavior = pinned
            ? [.canJoinAllSpaces, .ignoresCycle]
            : [.canJoinAllSpaces, .stationary, .ignoresCycle]
        settings?.level = pinned
            ? NSWindow.Level(rawValue: NSWindow.Level.floating.rawValue + 1)
            : .normal
    }
}
