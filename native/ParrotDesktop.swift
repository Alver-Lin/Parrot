import AppKit
import AVFoundation
import CoreGraphics
import Darwin
import Foundation
import UniformTypeIdentifiers
import WebKit

private let accent = NSColor(srgbRed: 0.26, green: 0.39, blue: 0.92, alpha: 1)

// Dock Genie uses the private window-server warp API.
// CGSSetWindowWarp requires float mesh points — CGPoint (Double) will silently no-op.
private typealias CGSConnectionID = Int32
private typealias CGSWindowID = Int32
private struct CGSMeshPoint {
    var x: Float
    var y: Float
}
private struct CGSWarpPoint {
    var local: CGSMeshPoint
    var global: CGSMeshPoint
}

@_silgen_name("CGSMainConnectionID")
private func CGSMainConnectionID() -> CGSConnectionID
@_silgen_name("_CGSDefaultConnection")
private func _CGSDefaultConnection() -> CGSConnectionID
@_silgen_name("CGSSetWindowWarp")
private func CGSSetWindowWarp(_ cid: CGSConnectionID, _ wid: CGSWindowID, _ w: Int32, _ h: Int32,
                              _ mesh: UnsafePointer<CGSWarpPoint>?) -> Int32
@_silgen_name("CGSSetWindowAlpha")
private func CGSSetWindowAlpha(_ cid: CGSConnectionID, _ wid: CGSWindowID, _ alpha: Float) -> Int32

private func cgsConnection() -> CGSConnectionID {
    let main = CGSMainConnectionID()
    return main != 0 ? main : _CGSDefaultConnection()
}

func waveform(in rect: NSRect, color: NSColor, heights: [CGFloat]? = nil) {
    color.setFill()
    let values = heights ?? [0.34, 0.76, 1, 0.55, 0.3]
    let width = rect.width / 9
    for (index, height) in values.enumerated() {
        let barHeight = rect.height * min(1.08, max(0.12, height))
        NSBezierPath(roundedRect: NSRect(x: rect.minX + CGFloat(index) * width * 1.8,
            y: rect.midY - barHeight / 2, width: width, height: barHeight),
            xRadius: width / 2, yRadius: width / 2).fill()
    }
}

func appImage(size: Int) -> NSImage {
    let dimension = CGFloat(size)
    let image = NSImage(size: NSSize(width: dimension, height: dimension))
    image.lockFocus()
    accent.setFill()
    NSBezierPath(roundedRect: NSRect(x: dimension * 0.07, y: dimension * 0.07,
        width: dimension * 0.86, height: dimension * 0.86),
        xRadius: dimension * 0.21, yRadius: dimension * 0.21).fill()
    waveform(in: NSRect(x: dimension * 0.24, y: dimension * 0.25,
        width: dimension * 0.52, height: dimension * 0.5), color: .white)
    image.unlockFocus()
    return image
}

final class FloatingPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

/// Native Dock-style Genie via CGSSetWindowWarp.
/// Mesh uses float points and Cocoa→CG (top-left) conversion, matching working GenieWarpMesh.
final class GenieEffectController {
    private enum Edge { case left, right, top, bottom }

    private var timer: Timer?
    private var completion: (() -> Void)?
    private weak var window: NSWindow?
    private var source = NSRect.zero
    private var target = NSRect.zero
    private var edge: Edge = .right
    private var collapsing = true
    private var startTime: CFTimeInterval = 0
    private let duration: CFTimeInterval = 0.5
    private(set) var isAnimating = false

    private var desktopMaxY: CGFloat {
        NSScreen.screens.map(\.frame.maxY).max() ?? 0
    }

    private func clearWarp(_ window: NSWindow, restoreAlpha: Bool = true) {
        let cid = cgsConnection()
        let wid = CGSWindowID(window.windowNumber)
        _ = CGSSetWindowWarp(cid, wid, 0, 0, nil)
        if restoreAlpha {
            _ = CGSSetWindowAlpha(cid, wid, 1)
            window.alphaValue = 1
        } else {
            // Keep fully transparent — restoring alpha while orderOut is still
            // flushing to WindowServer is what causes the end-of-collapse flash.
            _ = CGSSetWindowAlpha(cid, wid, 0)
            window.alphaValue = 0
        }
    }

    private func setWindowInvisible(_ window: NSWindow) {
        let cid = cgsConnection()
        let wid = CGSWindowID(window.windowNumber)
        _ = CGSSetWindowAlpha(cid, wid, 0)
        window.alphaValue = 0
    }

    private func setWindowOpaque(_ window: NSWindow) {
        let cid = cgsConnection()
        let wid = CGSWindowID(window.windowNumber)
        _ = CGSSetWindowAlpha(cid, wid, 1)
        window.alphaValue = 1
    }

    /// Hide + clear warp without restoring opacity. Defer clear so orderOut commits first.
    private func dismissCollapsed(_ window: NSWindow) {
        setWindowInvisible(window)
        window.orderOut(nil)
        DispatchQueue.main.async { [weak window] in
            guard let window else { return }
            self.clearWarp(window, restoreAlpha: false)
        }
    }

    func cancel() {
        timer?.invalidate()
        timer = nil
        if let window {
            if collapsing {
                dismissCollapsed(window)
            } else {
                clearWarp(window, restoreAlpha: true)
            }
        }
        window = nil
        let done = completion
        completion = nil
        isAnimating = false
        done?()
    }

    private func lerp(_ a: CGFloat, _ b: CGFloat, _ t: CGFloat) -> CGFloat { a + (b - a) * t }

    private func smoothstep(_ edge0: CGFloat, _ edge1: CGFloat, _ x: CGFloat) -> CGFloat {
        let t = min(max((x - edge0) / max(edge1 - edge0, 0.0001), 0), 1)
        return t * t * (3 - 2 * t)
    }

    private func easeInOut(_ t: CGFloat) -> CGFloat {
        t < 0.5 ? 2 * t * t : 1 - pow(-2 * t + 2, 2) / 2
    }

    private func resolveEdge(from source: NSRect, to target: NSRect) -> Edge {
        let dx = target.midX - source.midX
        let dy = target.midY - source.midY
        if abs(dx) >= abs(dy) { return dx >= 0 ? .right : .left }
        return dy >= 0 ? .top : .bottom
    }

    private func cubic(_ p0: CGPoint, _ p1: CGPoint, _ p2: CGPoint, _ p3: CGPoint, t: CGFloat) -> CGPoint {
        let u = 1 - t
        let uu = u * u
        let uuu = uu * u
        let tt = t * t
        let ttt = tt * t
        return CGPoint(
            x: uuu * p0.x + 3 * uu * t * p1.x + 3 * u * tt * p2.x + ttt * p3.x,
            y: uuu * p0.y + 3 * uu * t * p1.y + 3 * u * tt * p2.y + ttt * p3.y
        )
    }

    /// Progress 0 = normal window, 1 = fully poured into the slot.
    private func applyWarp(progress raw: CGFloat) {
        guard let window else { return }
        let progress = collapsing ? raw : (1 - raw)
        let frame = source
        let slot = target
        let screenH = desktopMaxY
        let winTop = screenH - frame.maxY
        let winBottom = screenH - frame.minY
        let slotTop = screenH - slot.maxY
        let slotBottom = screenH - slot.minY

        // Scale-to-fit the window aspect into the slot.
        let winAspect = frame.width / max(frame.height, 1)
        let slotAspect = slot.width / max(slot.height, 1)
        let fitWidth: CGFloat
        let fitHeight: CGFloat
        if winAspect > slotAspect {
            fitWidth = slot.width
            fitHeight = slot.width / winAspect
        } else {
            fitHeight = slot.height
            fitWidth = slot.height * winAspect
        }
        let fitLeft = slot.midX - fitWidth / 2
        let fitRight = slot.midX + fitWidth / 2
        let fitTop = (slotTop + slotBottom) / 2 - fitHeight / 2
        let fitBottom = fitTop + fitHeight

        // Trailing → near-edge Bezier funnels (CG / top-left space).
        let leftP0: CGPoint
        let leftP3: CGPoint
        let rightP0: CGPoint
        let rightP3: CGPoint
        switch edge {
        case .right:
            leftP0 = CGPoint(x: frame.minX, y: winTop)
            leftP3 = CGPoint(x: slot.minX, y: fitTop)
            rightP0 = CGPoint(x: frame.minX, y: winBottom)
            rightP3 = CGPoint(x: slot.minX, y: fitBottom)
        case .left:
            leftP0 = CGPoint(x: frame.maxX, y: winTop)
            leftP3 = CGPoint(x: slot.maxX, y: fitTop)
            rightP0 = CGPoint(x: frame.maxX, y: winBottom)
            rightP3 = CGPoint(x: slot.maxX, y: fitBottom)
        case .bottom:
            leftP0 = CGPoint(x: frame.minX, y: winTop)
            leftP3 = CGPoint(x: fitLeft, y: slotTop)
            rightP0 = CGPoint(x: frame.maxX, y: winTop)
            rightP3 = CGPoint(x: fitRight, y: slotTop)
        case .top:
            leftP0 = CGPoint(x: frame.minX, y: winBottom)
            leftP3 = CGPoint(x: fitLeft, y: slotBottom)
            rightP0 = CGPoint(x: frame.maxX, y: winBottom)
            rightP3 = CGPoint(x: fitRight, y: slotBottom)
        }

        func controls(p0: CGPoint, p3: CGPoint) -> (CGPoint, CGPoint) {
            switch edge {
            case .left, .right:
                let p1 = CGPoint(x: lerp(p0.x, p3.x, 0.45), y: p0.y)
                let p2 = CGPoint(x: lerp(p0.x, p3.x, 0.65), y: p3.y)
                return (p1, p2)
            case .top, .bottom:
                let p1 = CGPoint(x: p0.x, y: lerp(p0.y, p3.y, 0.45))
                let p2 = CGPoint(x: p3.x, y: lerp(p0.y, p3.y, 0.65))
                return (p1, p2)
            }
        }
        let (leftP1, leftP2) = controls(p0: leftP0, p3: leftP3)
        let (rightP1, rightP2) = controls(p0: rightP0, p3: rightP3)

        // Dock-like scheduling: bend first, then slide.
        let widthT = easeInOut(smoothstep(0, 0.45, progress))
        let slideT = easeInOut(smoothstep(0.12, 1.0, progress))

        // Along the travel axis use denser samples; cross axis stays lighter.
        let travelSteps = 24
        let crossSteps = 8
        let horizontal = edge == .left || edge == .right
        let cols = horizontal ? travelSteps : crossSteps
        let rows = horizontal ? crossSteps : travelSteps
        var mesh = [CGSWarpPoint](repeating: CGSWarpPoint(local: CGSMeshPoint(x: 0, y: 0),
                                                          global: CGSMeshPoint(x: 0, y: 0)),
                                  count: cols * rows)

        for travelIndex in 0..<travelSteps {
            let v = CGFloat(travelIndex) / CGFloat(travelSteps - 1) // 0 = trailing, 1 = leading
            // Stretch: trailing edge lags, leading edge advances first.
            let stretched = pow(v, 1 + (1 - widthT) * 1.6)
            let curveT = min(max(lerp(stretched, 1, slideT), 0), 1.35)
            let left = cubic(leftP0, leftP1, leftP2, leftP3, t: min(curveT, 1))
            let right = cubic(rightP0, rightP1, rightP2, rightP3, t: min(curveT, 1))
            let leftEnd: CGPoint
            let rightEnd: CGPoint
            if curveT > 1 {
                let s = min(curveT - 1, 0.35) / 0.35
                switch edge {
                case .right:
                    leftEnd = CGPoint(x: lerp(leftP3.x, slot.maxX, s), y: leftP3.y)
                    rightEnd = CGPoint(x: lerp(rightP3.x, slot.maxX, s), y: rightP3.y)
                case .left:
                    leftEnd = CGPoint(x: lerp(leftP3.x, slot.minX, s), y: leftP3.y)
                    rightEnd = CGPoint(x: lerp(rightP3.x, slot.minX, s), y: rightP3.y)
                case .bottom:
                    leftEnd = CGPoint(x: leftP3.x, y: lerp(leftP3.y, slotBottom, s))
                    rightEnd = CGPoint(x: rightP3.x, y: lerp(rightP3.y, slotBottom, s))
                case .top:
                    leftEnd = CGPoint(x: leftP3.x, y: lerp(leftP3.y, slotTop, s))
                    rightEnd = CGPoint(x: rightP3.x, y: lerp(rightP3.y, slotTop, s))
                }
            } else {
                leftEnd = left
                rightEnd = right
            }

            for crossIndex in 0..<crossSteps {
                let u = CGFloat(crossIndex) / CGFloat(crossSteps - 1)
                let global = CGPoint(
                    x: lerp(leftEnd.x, rightEnd.x, u),
                    y: lerp(leftEnd.y, rightEnd.y, u)
                )
                let localX: Float
                let localY: Float
                let index: Int
                if horizontal {
                    // Travel along X, cross along Y (top→bottom).
                    localX = Float(v * frame.width)
                    localY = Float(u * frame.height)
                    index = crossIndex * cols + travelIndex
                } else {
                    // Travel along Y, cross along X (left→right).
                    localX = Float(u * frame.width)
                    localY = Float(v * frame.height)
                    index = travelIndex * cols + crossIndex
                }
                mesh[index] = CGSWarpPoint(
                    local: CGSMeshPoint(x: localX, y: localY),
                    global: CGSMeshPoint(x: Float(global.x), y: Float(global.y))
                )
            }
        }

        let cid = cgsConnection()
        let wid = CGSWindowID(window.windowNumber)
        let result = CGSSetWindowWarp(cid, wid, Int32(cols), Int32(rows), mesh)
        if result != 0 {
            NSLog("[Parrot] CGSSetWindowWarp failed: %d (cid=%d wid=%d)", result, cid, wid)
        }
        // Fade earlier so the window is fully invisible well before warp is cleared.
        let alpha = Float(progress < 0.68 ? 1 : max(0, 1 - (progress - 0.68) / 0.22))
        _ = CGSSetWindowAlpha(cid, wid, alpha)
        window.alphaValue = CGFloat(alpha)
    }

    func animate(window: NSWindow, from source: NSRect, to target: NSRect, collapsing: Bool,
                 completion: @escaping () -> Void) {
        cancel()
        self.window = window
        self.source = source
        self.target = target
        self.edge = resolveEdge(from: source, to: target)
        self.collapsing = collapsing
        self.completion = completion
        isAnimating = true
        startTime = CACurrentMediaTime()

        if collapsing {
            setWindowOpaque(window)
            applyWarp(progress: 0)
        } else {
            window.setFrame(source, display: true)
            setWindowOpaque(window)
            window.orderFrontRegardless()
            applyWarp(progress: 0) // visual progress = 1 when expanding
        }

        let timer = Timer(timeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
            self?.tick()
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
        tick()
    }

    private func tick() {
        guard isAnimating, window != nil else { return }
        let progress = min(max((CACurrentMediaTime() - startTime) / duration, 0), 1)
        applyWarp(progress: CGFloat(progress))
        if progress >= 1 { finish() }
    }

    private func finish() {
        timer?.invalidate()
        timer = nil
        guard let window else {
            isAnimating = false
            let done = completion
            completion = nil
            done?()
            return
        }
        if collapsing {
            // Never restore alpha here — that flash is the full rectangle reappearing
            // for one frame after warp is removed but before orderOut lands.
            dismissCollapsed(window)
        } else {
            window.setFrame(source, display: true)
            clearWarp(window, restoreAlpha: true)
            window.makeKeyAndOrderFront(nil)
        }
        self.window = nil
        isAnimating = false
        let done = completion
        completion = nil
        done?()
    }
}

final class OrbView: NSView {
    var onClick: (() -> Void)?
    var onMove: ((NSPoint, Bool) -> Void)?
    var onMenu: ((NSEvent) -> Void)?
    var active = false {
        didSet {
            syncPulse()
            needsDisplay = true
        }
    }
    var paused = false { didSet { syncPulse(); needsDisplay = true } }
    var connected = false { didSet { syncPulse(); needsDisplay = true } }
    var level: CGFloat = 0 { didSet { needsDisplay = true } }
    private var startMouse = NSPoint.zero
    private var startOrigin = NSPoint.zero
    private var dragged = false
    private var phase: CGFloat = 0
    private var pulseTimer: Timer?
    var isAnimating: Bool { pulseTimer?.isValid == true }
    var indicatorColor: NSColor {
        if active && paused { return .systemOrange }
        return connected && active ? .systemGreen : NSColor(srgbRed: 0.72, green: 0.77, blue: 0.87, alpha: 1)
    }
    private var shouldAnimate: Bool { active && connected && !paused }

    deinit {
        pulseTimer?.invalidate()
    }

    private func syncPulse() {
        if shouldAnimate {
            guard pulseTimer == nil else { return }
            let timer = Timer(timeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
                guard let self else { return }
                self.phase += 0.3
                self.needsDisplay = true
            }
            RunLoop.main.add(timer, forMode: .common)
            pulseTimer = timer
        } else {
            pulseTimer?.invalidate()
            pulseTimer = nil
            phase = 0
        }
    }

    private func animatedHeights() -> [CGFloat] {
        let base: [CGFloat] = [0.34, 0.76, 1, 0.55, 0.3]
        guard shouldAnimate else { return base }
        let energy = max(0, min(level, 1))
        return base.enumerated().map { index, height in
            let primary = sin(phase * (1.35 + CGFloat(index) * 0.38) + CGFloat(index) * 1.05)
            let secondary = sin(phase * (2.1 + CGFloat(index) * 0.22) + CGFloat(index) * 0.7)
            let bounce = 0.5 + 0.35 * primary + 0.15 * secondary
            let floor: CGFloat = 0.28 + energy * 0.12
            let span: CGFloat = 0.48 + energy * 0.42
            let motion = floor + bounce * span
            return min(1.05, height * (0.42 + motion * 0.78))
        }
    }

    override func draw(_ dirtyRect: NSRect) {
        let ringInset: CGFloat = shouldAnimate ? 2 + sin(phase * 1.4) * 0.7 : 2
        let circle = bounds.insetBy(dx: 6, dy: 6)
        NSColor.white.withAlphaComponent(0.96).setFill()
        NSBezierPath(ovalIn: bounds.insetBy(dx: ringInset, dy: ringInset)).fill()
        accent.setFill()
        NSBezierPath(ovalIn: circle).fill()
        waveform(in: circle.insetBy(dx: 12, dy: 12), color: .white, heights: animatedHeights())
        let dot = NSRect(x: bounds.maxX - 17, y: 6, width: 10, height: 10)
        NSColor.white.setFill()
        NSBezierPath(ovalIn: dot.insetBy(dx: -2, dy: -2)).fill()
        indicatorColor.setFill()
        NSBezierPath(ovalIn: dot).fill()
    }

    override func mouseDown(with event: NSEvent) {
        startMouse = NSEvent.mouseLocation
        startOrigin = window?.frame.origin ?? .zero
        dragged = false
    }

    override func mouseDragged(with event: NSEvent) {
        let mouse = NSEvent.mouseLocation
        if hypot(mouse.x - startMouse.x, mouse.y - startMouse.y) > 4 { dragged = true }
        guard dragged else { return }
        onMove?(NSPoint(x: startOrigin.x + mouse.x - startMouse.x,
            y: startOrigin.y + mouse.y - startMouse.y), false)
    }

    override func mouseUp(with event: NSEvent) {
        if dragged { onMove?(window?.frame.origin ?? startOrigin, true) }
        else { onClick?() }
    }

    override func rightMouseDown(with event: NSEvent) { onMenu?(event) }

    override func accessibilityPerformPress() -> Bool { onClick?(); return true }
}

@MainActor
final class ParrotApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    let selfTesting = CommandLine.arguments.contains("--self-test")
    let token = UUID().uuidString + UUID().uuidString
    var orbPanel: NSPanel!
    var orbView: OrbView!
    var workspace: FloatingPanel!
    var webView: WKWebView!
    var statusItem: NSStatusItem!
    var menu: NSMenu!
    var backend: Process?
    var statusTimer: Timer?
    var lockFile: Int32 = -1
    var ownsLock = false
    var port = 0
    var serverReady = false
    var uiReady = false
    var quitting = false
    var selfTestFinished = false
    var pendingSettings = false
    var copiedForTest: String?
    var autoScrollForTest = true
    var audioSourceForTest = "system"
    var permissionSourcesForTest: [String] = []
    var systemPermissionForTest = false
    var microphonePermissionForTest = false
    var grantPermissionForTest = true
    var openedPrivacySourcesForTest: [String] = []
    lazy var polishSettings = PolishSettingsController(testing: selfTesting)
    var polishTask: Task<Void, Never>?
    var polishJobID: String?
    var screenshotInProgress = false
    var screenshotProcess: Process?
    var activity: NSObjectProtocol?
    var startupActivity: NSObjectProtocol?
    var lastDesktopState: Data?
    let genie = GenieEffectController()
    let localSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.connectionProxyDictionary = [:]
        configuration.timeoutIntervalForRequest = 2
        configuration.timeoutIntervalForResource = 60
        return URLSession(configuration: configuration)
    }()
    var dataDirectory: URL!
    var selfTestDirectory: URL!
    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        do {
            let env = ProcessInfo.processInfo.environment
            let defaultData = Bundle.main.object(forInfoDictionaryKey: "ParrotDataDirectory") as? String
                ?? NSHomeDirectory() + "/Library/Application Support/Parrot"
            dataDirectory = URL(fileURLWithPath: env["PARROT_DATA"] ?? defaultData, isDirectory: true)
            try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
            lockFile = Darwin.open(dataDirectory.appendingPathComponent("desktop.lock").path, O_CREAT | O_RDWR | O_CLOEXEC, 0o600)
            guard lockFile >= 0, flock(lockFile, LOCK_EX | LOCK_NB) == 0 else {
                let existing = NSRunningApplication.runningApplications(withBundleIdentifier: "com.alverlin.parrot").first { $0.processIdentifier != getpid() }
                existing?.activate(options: [.activateIgnoringOtherApps])
                NSApp.terminate(nil)
                return
            }
            ownsLock = true
            if selfTesting {
                selfTestDirectory = URL(fileURLWithPath: env["PARROT_SELF_TEST_OUTPUT"] ?? dataDirectory.path)
                try FileManager.default.createDirectory(at: selfTestDirectory, withIntermediateDirectories: true)
            }
            makeMenu()
            makeOrb()
            makeWorkspace()
            startupActivity = ProcessInfo.processInfo.beginActivity(options: .userInitiatedAllowingIdleSystemSleep, reason: "正在启动 Parrot 本机语音服务")
            try startBackend()
            writeDesktopState()
            Task { await waitForBackend() }
        } catch { failStartup(error.localizedDescription) }
    }

    func makeMenu() {
        menu = NSMenu()
        menu.addItem(withTitle: "显示 / 收起转写", action: #selector(toggleWorkspace), keyEquivalent: "")
        menu.addItem(withTitle: "识别设置…", action: #selector(openSettings), keyEquivalent: ",")
        menu.addItem(withTitle: "系统声音权限…", action: #selector(openCaptureSettings), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "退出 Parrot", action: #selector(quit), keyEquivalent: "q")
        for item in menu.items { item.target = self }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let image = NSImage(size: NSSize(width: 19, height: 18))
        image.lockFocus()
        waveform(in: NSRect(x: 2, y: 3, width: 15, height: 12), color: .black)
        image.unlockFocus()
        image.isTemplate = true
        statusItem.button?.image = image
        statusItem.button?.toolTip = "Parrot · 点击悬浮球开始学习"
        statusItem.menu = menu
        let appMenu = NSMenu()
        let rootItem = NSMenuItem()
        rootItem.submenu = menu
        appMenu.addItem(rootItem)
        let edit = NSMenu(title: "编辑")
        for (title, action, key) in [("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("剪切", "cut:", "x"), ("全选", "selectAll:", "a"), ("撤销", "undo:", "z")] {
            edit.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        editItem.submenu = edit
        appMenu.addItem(editItem)
        NSApp.mainMenu = appMenu
    }

    func makeOrb() {
        let savedScreen = selfTesting ? nil : UserDefaults.standard.object(forKey: "orbScreenNumber") as? NSNumber
        let screen = NSScreen.screens.first { savedScreen != nil && screenNumber($0) == savedScreen }
            ?? NSScreen.main ?? NSScreen.screens[0]
        let visible = screen.visibleFrame
        let horizontal = selfTesting ? nil : UserDefaults.standard.object(forKey: "orbHorizontalRatio") as? Double
        let vertical = selfTesting ? 0.5 : UserDefaults.standard.object(forKey: "orbVerticalRatio") as? Double ?? 0.5
        let origin = clampedOrbOrigin(NSPoint(x: visible.minX + (horizontal.map { max(0, visible.width - 64) * $0 } ?? 12),
            y: visible.minY + max(0, visible.height - 64) * vertical), in: visible)
        let frame = NSRect(origin: origin, size: NSSize(width: 64, height: 64))
        orbPanel = NSPanel(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        orbPanel.isOpaque = false
        orbPanel.backgroundColor = .clear
        orbPanel.hasShadow = true
        orbPanel.level = NSWindow.Level(rawValue: NSWindow.Level.floating.rawValue + 1)
        orbPanel.hidesOnDeactivate = false
        orbPanel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        orbPanel.isReleasedWhenClosed = false
        orbView = OrbView(frame: NSRect(origin: .zero, size: frame.size))
        orbView.toolTip = "点击展开 / 收起 Parrot；拖动调整悬浮球位置"
        orbView.setAccessibilityElement(true)
        orbView.setAccessibilityRole(.button)
        orbView.setAccessibilityLabel("Parrot 悬浮球，点击展开转写面板")
        orbView.onClick = { [weak self] in self?.toggleWorkspace() }
        orbView.onMove = { [weak self] point, finished in self?.moveOrb(to: point, finished: finished) }
        orbView.onMenu = { [weak self] event in
            guard let self = self else { return }
            NSMenu.popUpContextMenu(self.menu, with: event, for: self.orbView)
        }
        orbPanel.contentView = orbView
        orbPanel.orderFrontRegardless()
        NotificationCenter.default.addObserver(self, selector: #selector(screenDidChange), name: NSApplication.didChangeScreenParametersNotification, object: nil)
    }

    func screenNumber(_ screen: NSScreen) -> NSNumber? {
        screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber
    }

    func clampedOrbOrigin(_ point: NSPoint, in frame: NSRect) -> NSPoint {
        NSPoint(x: min(max(point.x, frame.minX), max(frame.minX, frame.maxX - 64)),
            y: min(max(point.y, frame.minY + 5), max(frame.minY + 5, frame.maxY - 69)))
    }

    @objc func screenDidChange() {
        moveOrb(to: orbPanel.frame.origin, finished: true)
    }

    func screenForOrb() -> NSScreen {
        let center = NSPoint(x: orbPanel.frame.midX, y: orbPanel.frame.midY)
        return NSScreen.screens.first { $0.frame.contains(center) } ?? NSScreen.main ?? NSScreen.screens[0]
    }

    func moveOrb(to point: NSPoint, finished: Bool) {
        let screen = NSScreen.screens.first { $0.frame.contains(NSPoint(x: point.x + 32, y: point.y + 32)) } ?? screenForOrb()
        let frame = screen.visibleFrame
        let origin = clampedOrbOrigin(point, in: frame)
        orbPanel.setFrameOrigin(origin)
        if finished && !selfTesting {
            UserDefaults.standard.set(Double((origin.x - frame.minX) / max(frame.width - 64, 1)), forKey: "orbHorizontalRatio")
            UserDefaults.standard.set(Double((origin.y - frame.minY) / max(frame.height - 64, 1)), forKey: "orbVerticalRatio")
            if let number = screenNumber(screen) { UserDefaults.standard.set(number, forKey: "orbScreenNumber") }
        }
        if workspace.isVisible { positionWorkspace() }
    }

    func makeWorkspace() {
        let visible = screenForOrb().visibleFrame
        workspace = FloatingPanel(contentRect: NSRect(x: 0, y: 0, width: min(620, visible.width - 100), height: min(740, visible.height - 70)),
            styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        workspace.title = "Parrot"
        workspace.titlebarAppearsTransparent = true
        workspace.backgroundColor = .white
        workspace.minSize = NSSize(width: 430, height: 540)
        workspace.level = .floating
        workspace.hidesOnDeactivate = false
        workspace.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        workspace.isReleasedWhenClosed = false
        workspace.delegate = self
        let controller = WKUserContentController()
        controller.add(self, name: "parrot")
        let tokenLiteral = jsonLiteral(token)
        let script = """
        (() => {
          const waiting = new Map();
          Object.defineProperty(window, '__PARROT_DESKTOP__', { value: Object.freeze({token: \(tokenLiteral), platform:'macOS'}), writable:false });
          window.parrotNativeReply = (id, value, error) => {
            const pending = waiting.get(id); if (!pending) return;
            waiting.delete(id); clearTimeout(pending.timer);
            error ? pending.reject(new Error(error)) : pending.resolve(value);
          };
          window.parrotNative = Object.freeze({
            send(action, payload={}) { window.webkit.messageHandlers.parrot.postMessage({...payload, action}); },
            request(action, payload={}) {
              return new Promise((resolve,reject) => {
                const id = crypto.randomUUID();
                const timer = setTimeout(() => {waiting.delete(id);reject(new Error('操作超时，请重试'));}, action === 'polishTextChunks' ? 1800000 : 300000);
                waiting.set(id,{resolve,reject,timer});
                window.webkit.messageHandlers.parrot.postMessage({...payload,action,id});
              });
            }
          });
        })();
        """
        controller.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = controller
        configuration.websiteDataStore = .nonPersistent()
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.underPageBackgroundColor = .white
        workspace.contentView = webView
    }

    func startBackend() throws {
        port = try unusedPort()
        guard let resources = Bundle.main.resourceURL,
              let executable = Bundle.main.executableURL else { throw appError("客户端资源缺失，请重新构建。") }
        let pythonPath = ProcessInfo.processInfo.environment["PARROT_PYTHON"]
            ?? Bundle.main.object(forInfoDictionaryKey: "ParrotPythonExecutable") as? String ?? ""
        guard FileManager.default.isExecutableFile(atPath: pythonPath) else {
            throw appError("未找到 Python 运行环境。请在项目目录运行 bash scripts/setup.sh 后重新构建客户端。")
        }
        let runtime = resources.appendingPathComponent("runtime")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonPath)
        process.currentDirectoryURL = runtime
        process.arguments = ["-m", "backend.desktop_server", "--port", String(port), "--parent-pid", String(getpid())]
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = runtime.path
        env["PYTHONPYCACHEPREFIX"] = dataDirectory.appendingPathComponent(".pycache").path
        env["PARROT_DATA"] = dataDirectory.path
        env["PARROT_DESKTOP_TOKEN"] = token
        env["PARROT_CAPTURE_BINARY"] = executable.deletingLastPathComponent().appendingPathComponent("ParrotCapture").path
        process.environment = env
        let logURL = dataDirectory.appendingPathComponent("desktop.log")
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
        let logDescriptor = Darwin.open(logURL.path, O_WRONLY | O_CREAT | O_APPEND, 0o600)
        guard logDescriptor >= 0 else { throw appError("无法创建客户端日志") }
        let log = FileHandle(fileDescriptor: logDescriptor, closeOnDealloc: true)
        process.standardOutput = log
        process.standardError = log
        try process.run()
        backend = process
    }

    func waitForBackend() async {
        let deadline = Date().addingTimeInterval(90)
        var loggedFailure = false
        while Date() < deadline {
            if backend?.isRunning != true { failStartup("语音服务启动失败。详细信息保存在数据目录的 desktop.log。"); return }
            do {
                let state = try await requestJSON("/api/status")
                serverReady = true
                applyState(state)
                webView.load(URLRequest(url: baseURL))
                statusTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                    Task { @MainActor in
                        guard let self = self, !self.quitting else { return }
                        if let state = try? await self.requestJSON("/api/status") { self.applyState(state) }
                        else { self.orbView.connected = false }
                    }
                }
                if selfTesting { showWorkspace() }
                return
            } catch {
                if !loggedFailure {
                    loggedFailure = true
                    logNative("本机服务连接：\(error.localizedDescription) [\((error as NSError).domain):\((error as NSError).code)]")
                }
            }
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        failStartup("语音服务启动超时，请退出并重新打开 Parrot。")
    }

    func logNative(_ text: String) {
        let url = dataDirectory.appendingPathComponent("desktop.log")
        let descriptor = Darwin.open(url.path, O_WRONLY | O_CREAT | O_APPEND, 0o600)
        guard descriptor >= 0 else { return }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        try? handle.write(contentsOf: Data(("[Parrot] " + text + "\n").utf8))
    }

    func applyState(_ state: [String: Any]) {
        orbView.connected = true
        orbView.paused = ["pausing", "paused", "resuming"].contains(state["active_status"] as? String ?? "")
        orbView.active = state["active_id"] is String
        orbView.level = CGFloat(state["level"] as? Double ?? 0)
        if orbView.active && activity == nil {
            activity = ProcessInfo.processInfo.beginActivity(options: .userInitiatedAllowingIdleSystemSleep, reason: "Parrot 正在进行语音转写")
        } else if !orbView.active, let activity = activity {
            ProcessInfo.processInfo.endActivity(activity)
            self.activity = nil
        }
        orbView.toolTip = orbView.paused ? "Parrot 已暂停，点击查看并继续"
            : orbView.active ? "Parrot 正在转写，点击查看；收起面板不会停止" : "点击展开 Parrot；拖动调整位置"
        writeDesktopState()
    }

    func writeDesktopState() {
        let state: [String: Any] = ["pid": Int(getpid()), "backend_pid": Int(backend?.processIdentifier ?? 0),
            "port": port, "ready": serverReady, "ui_ready": uiReady, "panel_visible": workspace.isVisible,
            "orb_visible": orbPanel.isVisible, "active": orbView.active, "paused": orbView.paused]
        guard let data = try? JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]), data != lastDesktopState else { return }
        try? data.write(to: dataDirectory.appendingPathComponent("desktop-state.json"), options: .atomic)
        lastDesktopState = data
    }

    func applicationWillTerminate(_ notification: Notification) {
        NotificationCenter.default.removeObserver(self)
        if ownsLock, let directory = dataDirectory {
            try? FileManager.default.removeItem(at: directory.appendingPathComponent("desktop-state.json"))
        }
    }

    func positionWorkspace() {
        let visible = screenForOrb().visibleFrame
        var frame = workspace.frame
        frame.size.width = min(frame.width, visible.width - 90)
        frame.size.height = min(frame.height, visible.height - 20)
        let rightSpace = visible.maxX - orbPanel.frame.maxX - 17
        let leftSpace = orbPanel.frame.minX - visible.minX - 17
        let available = max(rightSpace, leftSpace)
        if available >= workspace.minSize.width { frame.size.width = min(frame.width, available) }
        let rightX = orbPanel.frame.maxX + 9
        let leftX = orbPanel.frame.minX - frame.width - 9
        let preferredX = rightX + frame.width <= visible.maxX - 8 ? rightX
            : leftX >= visible.minX + 8 ? leftX : rightSpace >= leftSpace ? rightX : leftX
        frame.origin.x = min(max(preferredX, visible.minX + 8), visible.maxX - frame.width - 8)
        frame.origin.y = min(max(orbPanel.frame.midY - frame.height / 2, visible.minY + 8), visible.maxY - frame.height - 8)
        workspace.setFrame(frame, display: true)
    }

    @objc func toggleWorkspace() {
        if genie.isAnimating { return }
        if workspace.isVisible { hideWorkspace(animated: !selfTesting) }
        else { showWorkspace(animated: !selfTesting) }
    }

    func orbGenieFrame() -> NSRect {
        orbPanel.frame.insetBy(dx: 12, dy: 12)
    }

    func hideWorkspace(animated: Bool) {
        guard workspace.isVisible else { writeDesktopState(); return }
        if !animated || selfTesting {
            genie.cancel()
            workspace.orderOut(nil)
            writeDesktopState()
            return
        }
        let start = workspace.frame
        let end = orbGenieFrame()
        writeDesktopState()
        genie.animate(window: workspace, from: start, to: end, collapsing: true) { [weak self] in
            self?.writeDesktopState()
        }
    }

    func showWorkspace(animated: Bool = true) {
        if genie.isAnimating { return }
        positionWorkspace()
        NSApp.activate(ignoringOtherApps: true)
        if !serverReady { workspace.title = "Parrot · 正在启动…" }
        else { workspace.title = "Parrot" }
        let end = workspace.frame
        let start = orbGenieFrame()
        if !animated || selfTesting {
            workspace.makeKeyAndOrderFront(nil)
            writeDesktopState()
            return
        }
        writeDesktopState()
        genie.animate(window: workspace, from: end, to: start, collapsing: false) { [weak self] in
            self?.writeDesktopState()
        }
    }

    @objc func openCaptureSettings() {
        openAudioPrivacySettings(source: "system")
    }

    func openAudioPrivacySettings(source: String) {
        let pane = source == "microphone" ? "Privacy_Microphone" : "Privacy_ScreenCapture"
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") else { return }
        NSWorkspace.shared.open(url)
    }

    func requestAudioPermission(source: String, replyID: String?) async {
        if selfTesting {
            permissionSourcesForTest.append(source)
            if grantPermissionForTest {
                if source != "system" { microphonePermissionForTest = true }
                if source != "microphone" { systemPermissionForTest = true }
            }
            let missingSource = source != "system" && !microphonePermissionForTest ? "microphone" : "system"
            let resultSource = grantPermissionForTest ? (source == "microphone" ? "microphone" : "system") : missingSource
            reply(replyID, value: ["granted": grantPermissionForTest, "source": resultSource]); return
        }
        if source != "system" {
            let granted = await AVCaptureDevice.requestAccess(for: .audio)
            if !granted { reply(replyID, value: ["granted": false, "source": "microphone"]); return }
        }
        let granted = source == "microphone" || CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess()
        reply(replyID, value: ["granted": granted, "source": source == "microphone" ? "microphone" : "system"])
    }

    @objc func openSettings() {
        showWorkspace()
        if webView.isLoading || !serverReady { pendingSettings = true }
        else { webView.evaluateJavaScript("window.dispatchEvent(new Event('parrot-open-settings'))") }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        hideWorkspace(animated: !selfTesting)
        return false
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWorkspace(animated: !selfTesting)
        return true
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.frameInfo.securityOrigin.host == "127.0.0.1",
              message.frameInfo.securityOrigin.port == port,
              let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        let id = body["id"] as? String
        switch action {
        case "polishSettings":
            reply(id, value: polishSettings.snapshot())
        case "savePolishSettings":
            do { reply(id, value: try polishSettings.save(body)) }
            catch { reply(id, error: error.localizedDescription) }
        case "revealPolishKey":
            do { reply(id, value: try polishSettings.revealKey(for: body["base_url"] as? String ?? "")) }
            catch { reply(id, error: error.localizedDescription) }
        case "testPolishConnection":
            Task {
                do { reply(id, value: try await polishSettings.testConnection()) }
                catch { reply(id, error: error.localizedDescription) }
            }
        case "polishTextChunks":
            guard polishTask == nil, let chunks = body["chunks"] as? [String],
                  let jobID = body["job_id"] as? String, !jobID.isEmpty, jobID.count <= 80 else {
                reply(id, error: "已有润色任务进行中或请求无效"); return
            }
            polishJobID = jobID
            polishTask = Task {
                do {
                    let result = try await polishSettings.polishBlocks(chunks) { done, total in
                        let detail: [String: Any] = ["job_id": jobID, "done": done, "total": total]
                        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('parrot-polish-progress',{detail:\(jsonLiteral(detail))}))")
                    }
                    reply(id, value: ["chunks": result, "settings": polishSettings.snapshot()])
                } catch is CancellationError { reply(id, error: "已取消润色，原文未改动。") }
                catch { reply(id, error: error.localizedDescription) }
                polishTask = nil
                polishJobID = nil
            }
        case "cancelPolish":
            let matches = body["job_id"] as? String == polishJobID && polishTask != nil
            if matches { polishTask?.cancel() }
            reply(id, value: ["cancelled": matches])
        case "audioSource":
            if let source = body["source"] as? String {
                guard ["microphone", "system", "mixed"].contains(source) else {
                    reply(id, error: "音频来源无效"); return
                }
                if selfTesting { audioSourceForTest = source }
                else { UserDefaults.standard.set(source, forKey: "audioSource") }
            }
            reply(id, value: ["source": selfTesting ? audioSourceForTest
                              : UserDefaults.standard.string(forKey: "audioSource") ?? "system"])
        case "autoScroll":
            if selfTesting {
                if let enabled = body["enabled"] as? Bool { autoScrollForTest = enabled }
                reply(id, value: ["enabled": autoScrollForTest])
                return
            }
            let defaults = UserDefaults.standard
            if let enabled = body["enabled"] as? Bool {
                defaults.set(enabled, forKey: "autoScroll")
            }
            reply(id, value: ["enabled": defaults.object(forKey: "autoScroll") as? Bool ?? true])
        case "capturePermission":
            // Only a direct Start or authorization action requests access.
            let source = body["source"] as? String ?? "system"
            guard ["microphone", "system", "mixed"].contains(source) else {
                reply(id, error: "音频来源无效"); return
            }
            Task { await requestAudioPermission(source: source, replyID: id) }
        case "capturePermissionStatus":
            // Reading settings must never trigger a privacy prompt.
            reply(id, value: [
                "microphone": selfTesting ? microphonePermissionForTest
                    : AVCaptureDevice.authorizationStatus(for: .audio) == .authorized,
                "system": selfTesting ? systemPermissionForTest : CGPreflightScreenCaptureAccess(),
            ])
        case "openCaptureSettings":
            let source = body["source"] as? String ?? "system"
            if selfTesting { openedPrivacySourcesForTest.append(source) }
            else { openAudioPrivacySettings(source: source) }
            reply(id, value: ["opened": true])
        case "collapse": hideWorkspace(animated: !selfTesting)
        case "state":
            orbView.paused = body["paused"] as? Bool ?? false
            orbView.active = body["active"] as? Bool ?? false
            orbView.connected = body["connected"] as? Bool ?? false
            orbView.level = CGFloat(body["level"] as? Double ?? 0)
            uiReady = body["connected"] as? Bool ?? false
            if uiReady, let startupActivity = startupActivity {
                ProcessInfo.processInfo.endActivity(startupActivity)
                self.startupActivity = nil
            }
            writeDesktopState()
        case "copy":
            guard let text = body["text"] as? String else { reply(id, error: "没有可复制的文字"); return }
            if selfTesting { copiedForTest = text }
            else { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string) }
            reply(id, value: ["copied": true])
        case "screenshot":
            guard let sid = body["sessionId"] as? String else { reply(id, error: "截图记录不正确"); return }
            let segmentId = body["segmentId"] as? Int
            Task { await captureScreenshot(sid, segmentId: segmentId, replyID: id) }
        case "export":
            guard let sessionID = body["sessionId"] as? String else { reply(id, error: "记录不存在"); return }
            Task { await exportMarkdown(sessionID, title: body["title"] as? String ?? "学习记录", content: body["content"] as? String, replyID: id) }
        default: reply(id, error: "不支持的客户端操作")
        }
    }

    func exportFolderName(from title: String) -> String {
        var name = title.replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "\\", with: "_")
            .replacingOccurrences(of: ":", with: "_").trimmingCharacters(in: .whitespacesAndNewlines)
        if name.lowercased().hasSuffix(".md") { name = String(name.dropLast(3)) }
        return name.isEmpty ? "学习记录" : name
    }

    func exportMarkdown(_ sid: String, title: String, content: String?, replyID: String?) async {
        guard sid.count == 32, sid.allSatisfy({ $0.isHexDigit }) else { reply(replyID, error: "记录标识不正确"); return }
        do {
            let data: Data
            if let content = content {
                data = Data(content.utf8)
            } else {
                let (download, response) = try await request("/api/sessions/\(sid)/markdown")
                guard response.statusCode == 200 else { throw appError("无法导出这条记录") }
                data = download
            }
            let folderName = exportFolderName(from: title)
            if selfTesting {
                let destination = selfTestDirectory.appendingPathComponent("export-test")
                try await writeMarkdownExport(data, sid: sid, folder: destination)
                reply(replyID, value: ["saved": true])
                return
            }
            let panel = NSSavePanel()
            panel.title = "导出学习记录"
            panel.message = "将保存为一个文件夹，内含 Markdown 文档和 photo 截图目录"
            panel.nameFieldLabel = "导出为："
            panel.nameFieldStringValue = folderName
            panel.allowedContentTypes = [.folder]
            panel.canCreateDirectories = true
            panel.beginSheetModal(for: workspace) { [weak self] result in
                guard let self = self else { return }
                guard result == .OK, let destination = panel.url else { self.reply(replyID, value: ["saved": false]); return }
                Task {
                    do {
                        try await self.writeMarkdownExport(data, sid: sid, folder: destination)
                        self.reply(replyID, value: ["saved": true])
                    } catch { self.reply(replyID, error: error.localizedDescription) }
                }
            }
        } catch { reply(replyID, error: error.localizedDescription) }
    }

    func writeMarkdownExport(_ data: Data, sid: String, folder: URL) async throws {
        var root = folder
        if root.pathExtension.lowercased() == "md" {
            root = root.deletingPathExtension()
        }
        var isDirectory: ObjCBool = false
        if FileManager.default.fileExists(atPath: root.path, isDirectory: &isDirectory) {
            guard isDirectory.boolValue else { throw appError("导出位置已存在同名文件，请换一个名称") }
        } else {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        }
        let markdownFile = root.appendingPathComponent(root.lastPathComponent + ".md")
        let session = try await requestJSON("/api/sessions/\(sid)")
        let content = String(decoding: data, as: UTF8.self)
        var exportedPhotos = false
        for segment in session["segments"] as? [[String: Any]] ?? [] {
            guard segment["kind"] as? String == "image", let filename = segment["photo_filename"] as? String,
                  filename.range(of: "^[a-f0-9]{32}\\.png$", options: .regularExpression) != nil,
                  content.contains("photo/" + filename) else { continue }
            let (image, response) = try await request("/api/sessions/\(sid)/photo/\(filename)", timeout: 30)
            guard response.statusCode == 200 else { throw appError("导出截图失败，请重试") }
            let photoFolder = root.appendingPathComponent("photo", isDirectory: true)
            try FileManager.default.createDirectory(at: photoFolder, withIntermediateDirectories: true)
            let output = photoFolder.appendingPathComponent(filename)
            if FileManager.default.fileExists(atPath: output.path) {
                guard try Data(contentsOf: output) == image else { throw appError("photo 文件夹中存在不同内容的同名截图") }
            } else {
                try image.write(to: output, options: .atomic)
            }
            exportedPhotos = true
        }
        if !exportedPhotos {
            let photoFolder = root.appendingPathComponent("photo", isDirectory: true)
            try FileManager.default.createDirectory(at: photoFolder, withIntermediateDirectories: true)
        }
        try data.write(to: markdownFile, options: .atomic)
    }

    func captureScreenshot(_ sid: String, segmentId: Int? = nil, replyID: String?) async {
        guard sid.count == 32, sid.allSatisfy({ $0.isHexDigit }) else { reply(replyID, error: "记录标识不正确"); return }
        guard !screenshotInProgress else { reply(replyID, error: "请先完成当前截图"); return }
        screenshotInProgress = true
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("parrot-screenshot-\(UUID().uuidString).png")
        defer {
            screenshotInProgress = false
            screenshotProcess = nil
            try? FileManager.default.removeItem(at: file)
            if !quitting { orbPanel.orderFrontRegardless() }
            writeDesktopState()
        }
        do {
            _ = try await requestJSON("/api/sessions/\(sid)")
            guard selfTesting || CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess()
                else { throw appError("请在系统设置中允许 Parrot 的屏幕录制权限后再截图。") }
            workspace.orderOut(nil)
            orbPanel.orderOut(nil)
            writeDesktopState()
            try await Task.sleep(nanoseconds: 200_000_000)
            if selfTesting {
                let fixture = NSImage(size: NSSize(width: 240, height: 120))
                fixture.lockFocus()
                accent.setFill()
                NSBezierPath(rect: NSRect(x: 0, y: 0, width: 240, height: 120)).fill()
                ("Parrot screenshot test" as NSString).draw(at: NSPoint(x: 15, y: 50), withAttributes: [
                    .foregroundColor: NSColor.white, .font: NSFont.systemFont(ofSize: 18),
                ])
                fixture.unlockFocus()
                guard let tiff = fixture.tiffRepresentation,
                      let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:])
                    else { throw appError("无法生成截图") }
                try png.write(to: file)
            } else {
                let process = Process()
                screenshotProcess = process
                process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
                process.arguments = ["-i", "-s", "-x", "-t", "png", file.path]
                try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                    process.terminationHandler = { _ in continuation.resume() }
                    do { try process.run() }
                    catch { process.terminationHandler = nil; continuation.resume(throwing: error) }
                }
            }
            guard !quitting, FileManager.default.fileExists(atPath: file.path) else {
                reply(replyID, value: ["captured": false]); return
            }
            let size = try FileManager.default.attributesOfItem(atPath: file.path)[.size] as? NSNumber
            guard (size?.intValue ?? 0) <= 25 * 1024 * 1024 else { throw appError("截图过大，请缩小截图范围。") }
            let image = try Data(contentsOf: file)
            let path = segmentId.map { "/api/sessions/\(sid)/segments/\($0)/photo" }
                ?? "/api/sessions/\(sid)/screenshots"
            let expected = segmentId == nil ? 201 : 200
            let (body, response) = try await request(path, method: "POST", body: image,
                contentType: "image/png", timeout: 30)
            guard response.statusCode == expected else {
                let error = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
                throw appError(error?["detail"] as? String ?? "截图保存失败，请重试")
            }
            reply(replyID, value: ["captured": true])
        } catch {
            if !quitting { showWorkspace() }
            reply(replyID, error: error.localizedDescription)
        }
    }

    func reply(_ id: String?, value: Any = NSNull(), error: String? = nil) {
        guard let id = id else { return }
        webView.evaluateJavaScript("window.parrotNativeReply(\(jsonLiteral(id)),\(jsonLiteral(value)),\(jsonLiteral(error as Any? ?? NSNull())))")
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.host == "127.0.0.1", url.port == port { decisionHandler(.allow) }
        else {
            if navigationAction.navigationType == .linkActivated, url.scheme == "https" { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if pendingSettings { pendingSettings = false; openSettings() }
        if selfTesting { Task { await runSelfTest() } }
    }

    @objc func quit() { NSApp.terminate(nil) }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if selfTestFinished { return .terminateNow }
        if quitting { return .terminateCancel }
        quitting = true
        Task { await finishQuit() }
        return .terminateLater
    }

    func finishQuit() async {
        if let process = screenshotProcess, process.isRunning { process.terminate() }
        statusTimer?.invalidate()
        if let startupActivity = startupActivity { ProcessInfo.processInfo.endActivity(startupActivity); self.startupActivity = nil }
        if let activity = activity { ProcessInfo.processInfo.endActivity(activity); self.activity = nil }
        if let state = try? await requestJSON("/api/status"), let sid = state["active_id"] as? String {
            _ = try? await requestJSON("/api/sessions/\(sid)/stop", method: "POST")
            var deadline = Date().addingTimeInterval(30)
            workspace.title = "Parrot · 正在保存剩余转写…"
            while let state = try? await requestJSON("/api/status"), state["active_id"] is String {
                if Date() > deadline {
                    let alert = NSAlert()
                    alert.messageText = "剩余音频仍在转写"
                    alert.informativeText = "继续等待可保留剩余文字。立即退出可能丢失尚未识别的内容。"
                    alert.addButton(withTitle: "继续等待")
                    alert.addButton(withTitle: "立即退出")
                    if alert.runModal() == .alertSecondButtonReturn { break }
                    deadline = Date().addingTimeInterval(30)
                }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
        }
        await stopBackend()
        NSApp.reply(toApplicationShouldTerminate: true)
    }

    func stopBackend() async {
        guard let process = backend, process.isRunning else { return }
        process.interrupt()
        for _ in 0..<100 {
            if !process.isRunning { return }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        if process.isRunning { Darwin.kill(process.processIdentifier, SIGKILL) }
    }

    func request(_ path: String, method: String = "GET", body: Data? = nil,
                 contentType: String? = nil, timeout: TimeInterval = 2) async throws -> (Data, HTTPURLResponse) {
        guard let url = URL(string: path, relativeTo: baseURL) else { throw appError("请求地址不正确") }
        var request = URLRequest(url: url.absoluteURL)
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = timeout
        if let contentType = contentType { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        request.setValue(token, forHTTPHeaderField: "X-Parrot-Token")
        let (data, response) = try await localSession.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw appError("服务响应不正确") }
        return (data, response)
    }

    func requestJSON(_ path: String, method: String = "GET") async throws -> [String: Any] {
        let (data, response) = try await request(path, method: method)
        guard response.statusCode == 200 || response.statusCode == 202,
              let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw appError("语音服务暂不可用（HTTP \(response.statusCode)）") }
        return result
    }

    func failStartup(_ message: String) {
        if dataDirectory != nil { logNative("启动失败：" + message) }
        if selfTesting { print("SELF_TEST_FAILED: \(message)"); selfTestFinished = true; backend?.terminate(); exit(1) }
        let alert = NSAlert()
        alert.messageText = "Parrot 暂时无法启动"
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }

    func runSelfTest() async {
        do {
            try await PolishSettingsController.runSelfTest()
            for _ in 0..<60 {
                let ready = try? await webView.evaluateJavaScript("!!document.querySelector('[data-testid=desktop-workspace]') && !!window.parrotNative") as? Bool
                if ready == true { break }
                try await Task.sleep(nanoseconds: 200_000_000)
            }
            let ready = try await webView.evaluateJavaScript("!!document.querySelector('[data-testid=desktop-workspace]') && !!window.parrotNative") as? Bool
            guard ready == true else { throw appError("客户端界面或桥接未就绪") }
            guard orbPanel.isVisible, workspace.isVisible else { throw appError("悬浮球或面板未显示") }
            toggleWorkspace()
            guard !workspace.isVisible, orbPanel.isVisible else { throw appError("收起面板影响了悬浮球") }
            _ = try await requestJSON("/api/status")
            toggleWorkspace()
            guard workspace.isVisible else { throw appError("点击悬浮球未展开面板") }
            moveOrb(to: NSPoint(x: -10000, y: -10000), finished: true)
            let visible = screenForOrb().visibleFrame
            guard visible.contains(orbPanel.frame.origin) else { throw appError("悬浮球越界") }
            let freePoint = NSPoint(x: visible.minX + (visible.width - 64) * 0.55,
                y: visible.minY + (visible.height - 64) * 0.6)
            moveOrb(to: freePoint, finished: true)
            // AppKit aligns window origins to screen pixels, so allow sub-point rounding.
            guard abs(orbPanel.frame.minX - freePoint.x) <= 1,
                  abs(orbPanel.frame.minY - freePoint.y) <= 1 else { throw appError("松手后悬浮球没有停留在拖动位置") }
            moveOrb(to: NSPoint(x: visible.maxX - 64, y: freePoint.y), finished: true)
            guard workspace.frame.maxX <= orbPanel.frame.minX,
                  visible.contains(workspace.frame) else { throw appError("右侧悬浮球展开面板未正确避让") }
            let noPlayer = try await webView.evaluateJavaScript("!document.querySelector('audio') && !document.querySelector('input[type=file]')") as? Bool
            guard noPlayer == true else { throw appError("客户端不应显示播放器或文件导入") }
            _ = try await webView.callAsyncJavaScript("return await window.parrotNative.request('copy', {text:'Parrot client test'});", arguments: [:], in: nil, contentWorld: .page)
            guard copiedForTest == "Parrot client test" else { throw appError("原生复制桥接失败") }
            let (historyData, _) = try await request("/api/sessions")
            guard let history = try JSONSerialization.jsonObject(with: historyData) as? [[String: Any]],
                  let sid = history.first?["id"] as? String else { throw appError("测试历史记录不存在") }
            let initialOrb = OrbView(frame: NSRect(x: 0, y: 0, width: 64, height: 64))
            let idleColor = NSColor(srgbRed: 0.72, green: 0.77, blue: 0.87, alpha: 1)
            guard initialOrb.indicatorColor == idleColor && !initialOrb.isAnimating
                else { throw appError("首次启动状态点应为灰色且不律动") }
            initialOrb.connected = true
            guard initialOrb.indicatorColor == idleColor && !initialOrb.isAnimating
                else { throw appError("连接就绪但未录制时应保持灰色") }
            applyState(["active_id": sid, "active_status": "recording", "level": 0.7])
            guard orbView.isAnimating && orbView.indicatorColor == .systemGreen else { throw appError("录制状态应为绿色并律动") }
            for phase in ["pausing", "paused", "resuming"] {
                applyState(["active_id": sid, "active_status": phase, "level": 0])
                guard orbView.paused && !orbView.isAnimating && orbView.indicatorColor == .systemOrange
                    else { throw appError("暂停及恢复准备期间应为橙色并停止律动") }
            }
            let pausedBitmap = orbView.bitmapImageRepForCachingDisplay(in: orbView.bounds)!
            orbView.cacheDisplay(in: orbView.bounds, to: pausedBitmap)
            if let png = pausedBitmap.representation(using: .png, properties: [:]) {
                try png.write(to: selfTestDirectory.appendingPathComponent("desktop-orb-paused.png"))
            }
            applyState(["active_id": sid, "active_status": "recording", "level": 0.7])
            guard !orbView.paused && orbView.isAnimating && orbView.indicatorColor == .systemGreen
                else { throw appError("继续录制后应恢复绿色及律动") }
            applyState(["level": 0])
            _ = try await webView.callAsyncJavaScript("return await window.parrotNative.request('export', {sessionId:sid, title:'客户端验证'});", arguments: ["sid": sid], in: nil, contentWorld: .page)
            let exported = try String(contentsOf: selfTestDirectory.appendingPathComponent("export-test/export-test.md"), encoding: .utf8)
            guard exported.contains("English words") else { throw appError("原生导出内容不正确") }
            let historyWorks = try await webView.callAsyncJavaScript("""
              document.querySelector('[aria-label="历史记录"]').click();
              await new Promise(resolve => setTimeout(resolve, 350));
              const item = document.querySelector('.history-item');
              if (!item) return false;
              item.click();
              await new Promise(resolve => setTimeout(resolve, 350));
              return document.querySelector('.segment-content')?.textContent.includes('English words');
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard historyWorks == true else { throw appError("客户端历史记录未正确展示") }
            let continuationWorks = try await webView.callAsyncJavaScript("""
              const originalFetch = window.fetch;
              let continued = false;
              let created = false;
              window.fetch = async (input, options) => {
                const path = new URL(String(input), location.href).pathname;
                if (options?.method === 'POST' && path === '/api/sessions/' + sid + '/continue') {
                  continued = true;
                  // Verify routing without requesting real system audio in a UI test.
                  return originalFetch('/api/sessions/' + sid, {headers: options.headers});
                }
                if (options?.method === 'POST' && path === '/api/sessions') {
                  created = true;
                  throw new Error('历史记录不应新建任务');
                }
                return originalFetch(input, options);
              };
              try {
                document.querySelector('[aria-label="续写当前记录"]')?.click();
                await new Promise(resolve => setTimeout(resolve, 700));
                return continued && !created && document.querySelector('.segment-content')?.textContent.includes('English words');
              } finally {
                window.fetch = originalFetch;
              }
            """, arguments: ["sid": sid], in: nil, contentWorld: .page) as? Bool
            guard continuationWorks == true else { throw appError("历史记录的麦克风按钮没有使用续写入口") }
            let markdownWorks = try await webView.callAsyncJavaScript("""
              const tab = Array.from(document.querySelectorAll('[role="tab"]')).find(node => node.textContent.includes('Markdown'));
              if (!tab) return false;
              tab.click();
              await new Promise(resolve => setTimeout(resolve, 100));
              return document.querySelector('.markdown-editor')?.value.includes('English words');
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard markdownWorks == true else { throw appError("客户端 Markdown 未正确展示") }
            let editedMarkdown = "# 直接编辑验证\n\n**Markdown** English words\n\n"
            let markdownEditWorks = try await webView.callAsyncJavaScript("""
              const editor = document.querySelector('.markdown-editor');
              editor.value = content + 'draft';
              editor.dispatchEvent(new Event('input', {bubbles: true}));
              await new Promise(resolve => setTimeout(resolve, 900));
              editor.dispatchEvent(new Event('blur'));
              editor.value = content;
              editor.dispatchEvent(new Event('input', {bubbles: true}));
              await new Promise(resolve => setTimeout(resolve, 50));
              document.querySelector('[aria-label="复制全文"]').click();
              const exportButton = Array.from(document.querySelectorAll('button')).find(node => node.textContent.includes('导出'));
              exportButton.click();
              await new Promise(resolve => setTimeout(resolve, 1500));
              return document.querySelector('.markdown-editor')?.value === content
                && !document.querySelector('[aria-label="保存 Markdown"]');
            """, arguments: ["content": editedMarkdown], in: nil, contentWorld: .page) as? Bool
            guard markdownEditWorks == true else { throw appError("Markdown 编辑或自动保存失败") }
            guard copiedForTest == editedMarkdown else { throw appError("复制没有使用编辑后的 Markdown") }
            let editedExport = try String(contentsOf: selfTestDirectory.appendingPathComponent("export-test/export-test.md"), encoding: .utf8)
            guard editedExport == editedMarkdown else { throw appError("导出没有使用编辑后的 Markdown") }
            let (editedData, _) = try await request("/api/sessions/\(sid)")
            let editedSession = try JSONSerialization.jsonObject(with: editedData) as? [String: Any]
            guard editedSession?["markdown_content"] as? String == editedMarkdown else { throw appError("Markdown 未持久保存") }
            guard editedSession?["title"] as? String == "直接编辑验证" else { throw appError("记录名称未跟随 Markdown 标题") }
            let screenshotInserted = try await webView.callAsyncJavaScript("""
              if (document.querySelector('[aria-label="插入截图"]')) return false;
              const originalFetch = window.fetch;
              // Simulate an active paused record without acquiring real audio.
              window.fetch = async (input, options) => {
                const response = await originalFetch(input, options);
                const path = new URL(String(input), location.href).pathname;
                if (options?.method && options.method !== 'GET') return response;
                if (path !== '/api/status' && path !== '/api/sessions/' + sid) return response;
                const body = await response.json();
                if (path === '/api/status') {
                  body.active_id = sid;
                  body.active_status = 'paused';
                } else {
                  body.status = 'paused';
                }
                return new Response(JSON.stringify(body), {headers:{'Content-Type':'application/json'}});
              };
              try {
                await new Promise(resolve => setTimeout(resolve, 1500));
                const buttons = ['插入截图', '继续聆听', '停止聆听'].map(label => document.querySelector('[aria-label="' + label + '"]'));
                if (buttons.some(button => !button)) return false;
                const bounds = buttons.map(button => button.getBoundingClientRect());
                if (bounds.some(rect => Math.abs(rect.width - 44) > 0.5 || Math.abs(rect.height - 44) > 0.5
                  || Math.abs(rect.top - bounds[0].top) > 0.5)) return false;
                buttons[0].click();
                await new Promise(resolve => setTimeout(resolve, 1800));
                const image = document.querySelector('.transcript-photo img');
                return !!image && image.complete && image.naturalWidth > 0;
              } finally {
                window.fetch = originalFetch;
              }
            """, arguments: ["sid": sid], in: nil, contentWorld: .page) as? Bool
            guard screenshotInserted == true else { throw appError("截图没有插入转写内容或无法显示") }
            guard !workspace.isVisible && orbPanel.isVisible else { throw appError("截图后应保持看板收起并显示悬浮球") }
            showWorkspace()
            let (photoSessionData, _) = try await request("/api/sessions/\(sid)")
            let photoSession = try JSONSerialization.jsonObject(with: photoSessionData) as? [String: Any]
            let photoSegments = photoSession?["segments"] as? [[String: Any]] ?? []
            guard let photo = photoSegments.first(where: { $0["kind"] as? String == "image" }),
                  let photoFilename = photo["photo_filename"] as? String else { throw appError("截图没有持久保存") }
            let (photoData, photoResponse) = try await request("/api/sessions/\(sid)/photo/\(photoFilename)")
            guard photoResponse.statusCode == 200 else { throw appError("截图接口不能读取图片") }
            _ = try await webView.callAsyncJavaScript("""
              const button = Array.from(document.querySelectorAll('button')).find(node => node.textContent.includes('导出'));
              button.click();
              await new Promise(resolve => setTimeout(resolve, 600));
              return true;
            """, arguments: [:], in: nil, contentWorld: .page)
            let photoExport = try String(contentsOf: selfTestDirectory.appendingPathComponent("export-test/export-test.md"), encoding: .utf8)
            let exportedPhoto = try Data(contentsOf: selfTestDirectory.appendingPathComponent("export-test/photo/\(photoFilename)"))
            guard photoExport.contains("photo/" + photoFilename) && exportedPhoto == photoData
                else { throw appError("Markdown 导出未包含相对图片引用及 photo 文件夹") }
            let historyTitleWorks = try await webView.callAsyncJavaScript("""
              document.querySelector('[aria-label="历史记录"]').click();
              await new Promise(resolve => setTimeout(resolve, 350));
              return document.querySelector('.history-item')?.textContent.includes('直接编辑验证');
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard historyTitleWorks == true else { throw appError("历史记录名称没有更新") }
            _ = try await webView.evaluateJavaScript("document.querySelector('.ant-modal-close')?.click()")
            try await Task.sleep(nanoseconds: 300_000_000)
            let unauthorized = try await URLSession.shared.data(from: baseURL.appendingPathComponent("api/status"))
            guard (unauthorized.1 as? HTTPURLResponse)?.statusCode == 403 else { throw appError("语音接口缺少客户端鉴权") }
            let permissionRequestsBeforeSettings = permissionSourcesForTest.count
            openSettings()
            try await Task.sleep(nanoseconds: 300_000_000)
            let settingsVisible = try await webView.evaluateJavaScript("!!document.querySelector('.ant-modal-content')") as? Bool
            guard settingsVisible == true else { throw appError("设置面板未打开") }
            let audioSourcesWork = try await webView.callAsyncJavaScript("""
              const labels = ['识别麦克风音频', '识别本机音频', '识别麦克风与本机音频'];
              const sources = ['microphone', 'system', 'mixed'];
              const select = document.querySelector('#audio-source-select')?.closest('.ant-select');
              if (!select || !select.textContent.includes('识别本机音频')) return false;
              for (let i = 0; i < sources.length; i++) {
                select.querySelector('.ant-select-selector').dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                await new Promise(resolve => setTimeout(resolve, 150));
                const option = Array.from(document.querySelectorAll('.ant-select-item-option'))
                  .find(node => node.title === labels[i]);
                if (!option) return false;
                option.click();
                await new Promise(resolve => setTimeout(resolve, 200));
                const saved = await window.parrotNative.request('audioSource');
                if (saved.source !== sources[i] || !select.textContent.includes(labels[i])) return false;
              }
              return true;
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard audioSourcesWork == true else { throw appError("音频来源三选项未正确展示或保存") }
            guard permissionSourcesForTest.count == permissionRequestsBeforeSettings
                else { throw appError("查看授权状态不应自动请求权限") }
            grantPermissionForTest = false
            let microphonePermissionAction = try await webView.callAsyncJavaScript("""
              await new Promise(resolve => setTimeout(resolve, 1300));
              const row = document.querySelector('.permission-status');
              if (!row?.querySelector('.status-cross') || row.querySelector('.status-dot')
                || !row.textContent.includes('缺少麦克风权限')) return false;
              row.querySelector('button').click();
              await new Promise(resolve => setTimeout(resolve, 300));
              return row.textContent.includes('程序未授权');
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard microphonePermissionAction == true, openedPrivacySourcesForTest.last == "microphone"
                else { throw appError("麦克风未授权时应显示红叉并前往麦克风授权") }
            if let image = try? await webView.takeSnapshot(configuration: nil),
               let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
               let png = bitmap.representation(using: .png, properties: [:]) {
                try png.write(to: selfTestDirectory.appendingPathComponent("desktop-settings-unready.png"))
            }
            microphonePermissionForTest = true
            systemPermissionForTest = false
            let systemPermissionAction = try await webView.callAsyncJavaScript("""
              await new Promise(resolve => setTimeout(resolve, 1300));
              const row = document.querySelector('.permission-status');
              if (!row?.textContent.includes('缺少本机音频权限')) return false;
              row.querySelector('button').click();
              await new Promise(resolve => setTimeout(resolve, 300));
              return !!row.querySelector('.status-cross');
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard systemPermissionAction == true, openedPrivacySourcesForTest.last == "system"
                else { throw appError("本机音频未授权时应前往系统声音授权") }
            grantPermissionForTest = true
            let permissionReady = try await webView.callAsyncJavaScript("""
              const row = document.querySelector('.permission-status');
              row.querySelector('button').click();
              await new Promise(resolve => setTimeout(resolve, 400));
              return row.textContent.includes('程序已授权') && !!row.querySelector('.status-dot')
                && !row.querySelector('.status-cross') && !row.querySelector('button');
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard permissionReady == true else { throw appError("双声源所需权限全部开启后应显示绿色圆圈") }
            try await Task.sleep(nanoseconds: 500_000_000)
            if let image = try? await webView.takeSnapshot(configuration: nil),
               let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
               let png = bitmap.representation(using: .png, properties: [:]) {
                try png.write(to: selfTestDirectory.appendingPathComponent("desktop-settings.png"))
            }
            let downloadedModelStatus = try await webView.callAsyncJavaScript("""
              const originalFetch = window.fetch;
              let phase = 'missing';
              let downloadRequests = 0;
              let requestedSettings;
              window.fetch = async (input, options) => {
                const path = new URL(String(input), location.href).pathname;
                if (path === '/api/model/prepare' && options?.method === 'POST') {
                  downloadRequests++;
                  requestedSettings = JSON.parse(options.body);
                  phase = 'loading';
                  return new Response(JSON.stringify({message:'fixture'}), {status:202, headers:{'Content-Type':'application/json'}});
                }
                const response = await originalFetch(input, options);
                if (path !== '/api/status') return response;
                const body = await response.json();
                body.model = {status:phase === 'loading' ? 'loading' : phase === 'failed' ? 'error' : 'idle',
                  message:phase === 'failed' ? '模型下载测试失败' : '', engine:body.model_cache.auto_engine, model:'large-v3'};
                body.model_cache.downloaded[body.model_cache.auto_engine]['large-v3'] = phase === 'downloaded';
                return new Response(JSON.stringify(body), {headers:{'Content-Type':'application/json'}});
              };
              async function waitFor(predicate) {
                for (let i = 0; i < 40; i++) {
                  if (predicate()) return true;
                  await new Promise(resolve => setTimeout(resolve, 100));
                }
                return false;
              }
              try {
                await new Promise(resolve => setTimeout(resolve, 1300));
                const section = document.querySelector('.model-preparation');
                if (!section?.querySelector('.status-cross') || !section.querySelector('button')
                  || section.querySelector('.status-dot')) return false;
                section.querySelector('button').click();
                if (!await waitFor(() => downloadRequests === 1 && section.textContent.includes('模型下载中')
                  && section.querySelector('button')?.disabled)) return false;
                if (requestedSettings.model !== 'large-v3' || requestedSettings.engine !== 'auto') return false;
                phase = 'failed';
                if (!await waitFor(() => section.textContent.includes('模型下载失败')
                  && !!section.querySelector('.status-cross') && !section.querySelector('button')?.disabled)) return false;
                section.querySelector('button').click();
                if (!await waitFor(() => downloadRequests === 2)) return false;
                phase = 'downloaded';
                return await waitFor(() => section.textContent.includes('模型已下载')
                  && !!section.querySelector('.status-dot') && !section.querySelector('.status-cross') && !section.querySelector('button'));
              } finally {
                window.fetch = originalFetch;
              }
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard downloadedModelStatus == true else { throw appError("模型状态、下载入口、失败重试或绿色成功圆圈未正确展示") }
            let polishSettingsWork = try await webView.callAsyncJavaScript("""
              const row = document.querySelector('.polish-model-status');
              if (!row?.textContent.includes('润色模型未配置') || !row.querySelector('.status-cross')) return false;
              const fill = (id, value) => {
                const input = document.getElementById(id);
                input.value = value;
                input.dispatchEvent(new Event('input', {bubbles:true}));
              };
              fill('polish-base-url', 'https://api.example.com/compatible-mode/v1/');
              fill('polish-model', 'parrot-polish-test');
              fill('polish-api-key', 'parrot-ui-fixture-secret');
              await new Promise(resolve => setTimeout(resolve, 100));
              if (document.querySelector('[aria-label="保存 AI 配置"]')
                || !document.querySelector('.polish-connection-status').textContent.includes('待测试')) return false;
              document.querySelector('[aria-label="测试 AI 连接"]').click();
              await new Promise(resolve => setTimeout(resolve, 400));
              const saved = await window.parrotNative.request('polishSettings');
              if (saved.model !== 'parrot-polish-test' || !saved.has_api_key || saved.api_key
                || JSON.stringify(saved).includes('parrot-ui-fixture-secret')) return false;
              const keyInput = document.getElementById('polish-api-key');
              if (keyInput.value !== 'parrot-ui-fixture-secret' || keyInput.type !== 'password'
                || document.querySelector('.polish-remove-key')) return false;
              document.querySelector('[aria-label="显示 API Key"]').click();
              await new Promise(resolve => setTimeout(resolve, 200));
              if (keyInput.value !== 'parrot-ui-fixture-secret' || keyInput.type !== 'text'
                || !document.querySelector('.polish-connection-status.connected')?.textContent.includes('连接成功')) return false;
              document.querySelector('[aria-label="隐藏 API Key"]').click();
              await new Promise(resolve => setTimeout(resolve, 100));
              if (keyInput.value !== 'parrot-ui-fixture-secret' || keyInput.type !== 'password') return false;
              document.querySelector('[aria-label="显示 API Key"]').click();
              await new Promise(resolve => setTimeout(resolve, 200));
              document.querySelector('.polish-configuration').closest('.ant-modal').querySelector('.ant-modal-close').click();
              await new Promise(resolve => setTimeout(resolve, 300));
              window.dispatchEvent(new Event('parrot-open-settings'));
              await new Promise(resolve => setTimeout(resolve, 300));
              if (keyInput.value !== 'parrot-ui-fixture-secret' || keyInput.type !== 'text') return false;
              document.querySelector('[aria-label="隐藏 API Key"]').click();
              fill('polish-api-key', '');
              fill('polish-model', 'parrot-polish-test-2');
              await new Promise(resolve => setTimeout(resolve, 100));
              document.querySelector('[aria-label="测试 AI 连接"]').click();
              await new Promise(resolve => setTimeout(resolve, 400));
              if (keyInput.value !== 'parrot-ui-fixture-secret') return false;
              fill('polish-api-key', 'parrot-ui-fixture-replacement');
              await new Promise(resolve => setTimeout(resolve, 100));
              document.querySelector('[aria-label="测试 AI 连接"]').click();
              await new Promise(resolve => setTimeout(resolve, 400));
              if (keyInput.value !== 'parrot-ui-fixture-replacement') return false;
              const replacement = await window.parrotNative.request('revealPolishKey', {base_url:saved.base_url});
              if (replacement.api_key !== 'parrot-ui-fixture-replacement') return false;
              const tested = await window.parrotNative.request('polishSettings');
              return tested.status === 'connected' && row.textContent.includes('润色模型已连接')
                && !!row.querySelector('.status-dot') && !row.querySelector('.status-cross')
                && row.textContent.includes('无需下载');
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard polishSettingsWork == true else { throw appError("AI 测试连接自动保存、密钥回填、显示隐藏、空白保留或替换密钥状态失败") }
            polishSettings.responseStatusForTest = 401
            let polishFailureWorks = try await webView.callAsyncJavaScript("""
              const keyInput = document.getElementById('polish-api-key');
              keyInput.value = 'parrot-ui-failed-fixture';
              keyInput.dispatchEvent(new Event('input', {bubbles:true}));
              await new Promise(resolve => setTimeout(resolve, 100));
              document.querySelector('[aria-label="测试 AI 连接"]').click();
              await new Promise(resolve => setTimeout(resolve, 400));
              const row = document.querySelector('.polish-model-status');
              const settings = await window.parrotNative.request('polishSettings');
              const stored = await window.parrotNative.request('revealPolishKey', {base_url:settings.base_url});
              return row.textContent.includes('润色模型连接失败') && row.textContent.includes('401')
                && !!row.querySelector('.status-cross') && !row.querySelector('.status-dot')
                && document.querySelector('.polish-connection-status.failed')?.textContent.includes('连接失败')
                && stored.api_key === 'parrot-ui-failed-fixture' && keyInput.value === stored.api_key;
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard polishFailureWorks == true else { throw appError("润色连接失败时应显示红叉及错误原因") }
            polishSettings.responseStatusForTest = 200
            _ = try await webView.callAsyncJavaScript("""
              document.querySelector('[aria-label="测试 AI 连接"]').click();
              await new Promise(resolve => setTimeout(resolve, 3500));
              document.querySelector('.recognition-status').scrollIntoView({block:'end'});
            """, arguments: [:], in: nil, contentWorld: .page)
            if let image = try? await webView.takeSnapshot(configuration: nil),
               let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
               let png = bitmap.representation(using: .png, properties: [:]) {
                try png.write(to: selfTestDirectory.appendingPathComponent("desktop-settings-ai.png"))
            }
            let autoScrollWorks = try await webView.callAsyncJavaScript("""
              const toggle = document.querySelector('#auto-scroll-switch');
              if (!toggle || toggle.getAttribute('aria-checked') !== 'true') return false;
              if (document.querySelector('.panel-footer input[type=checkbox]')) return false;
              toggle.click();
              await new Promise(resolve => setTimeout(resolve, 200));
              const preference = await window.parrotNative.request('autoScroll');
              return preference.enabled === false && toggle.getAttribute('aria-checked') === 'false';
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard autoScrollWorks == true else { throw appError("识别设置中的自动跟随开关未正确保存") }
            _ = try await webView.evaluateJavaScript("document.querySelector('#auto-scroll-switch')?.closest('.ant-modal-content')?.querySelector('.ant-modal-close')?.click()")
            try await Task.sleep(nanoseconds: 300_000_000)
            let sourceRequestWorks = try await webView.callAsyncJavaScript("""
              document.querySelector('[aria-label="新建学习记录"]').click();
              await new Promise(resolve => setTimeout(resolve, 150));
              const originalFetch = window.fetch;
              let source;
              window.fetch = async (input, options) => {
                if (options?.method === 'POST' && new URL(String(input), location.href).pathname === '/api/sessions') {
                  source = JSON.parse(options.body).audio_source;
                  return originalFetch('/api/sessions/' + sid, {headers: options.headers});
                }
                return originalFetch(input, options);
              };
              try {
                document.querySelector('[aria-label="开始聆听"]').click();
                await new Promise(resolve => setTimeout(resolve, 650));
                return source === 'mixed';
              } finally { window.fetch = originalFetch; }
            """, arguments: ["sid": sid], in: nil, contentWorld: .page) as? Bool
            guard sourceRequestWorks == true, permissionSourcesForTest.first == "system",
                  permissionSourcesForTest.last == "mixed" else { throw appError("新记录未使用所选音频来源及对应权限") }
            let originalPolishDocument = try await requestJSON("/api/sessions/\(sid)")
            let originalPolishText = originalPolishDocument["markdown_content"] as? String ?? ""
            let expectedPhoto = "photo/" + photoFilename
            let requestCountBeforePolish = polishSettings.requestsForTest.count
            let polishPreviewWorks = try await webView.callAsyncJavaScript("""
              document.querySelector('#rc-tabs-0-tab-markdown')?.click();
              await new Promise(resolve => setTimeout(resolve, 150));
              document.querySelector('[aria-label="AI 润色"]').click();
              await new Promise(resolve => setTimeout(resolve, 300));
              const privacy = document.querySelector('.polish-privacy');
              if (!privacy?.textContent.includes('不上传、不读图')) return false;
              document.querySelector('[aria-label="开始 AI 润色"]').click();
              await new Promise(resolve => setTimeout(resolve, 500));
              const panes = document.querySelectorAll('.polish-comparison section');
              const apply = document.querySelector('[aria-label="应用 AI 润色"]');
              return panes.length === 2 && !!apply && !apply.disabled && panes[1].textContent.includes('已润色')
                && panes[0].querySelectorAll('.transcript-photo').length === 1
                && panes[1].querySelectorAll('.transcript-photo').length === 1;
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard polishPreviewWorks == true else { throw appError("AI 润色按钮、图文预览或应用入口无效") }
            let beforeApply = try await requestJSON("/api/sessions/\(sid)")
            guard beforeApply["markdown_content"] as? String == originalPolishText else { throw appError("预览不应自动覆盖文稿") }
            for request in polishSettings.requestsForTest.dropFirst(requestCountBeforePolish) {
                let body = String(data: request.httpBody ?? Data(), encoding: .utf8) ?? ""
                guard !body.contains(expectedPhoto), !body.contains(photoFilename), !body.contains("![截图")
                    else { throw appError("截图引用不应发送到润色 API") }
            }
            if let image = try? await webView.takeSnapshot(configuration: nil),
               let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
               let png = bitmap.representation(using: .png, properties: [:]) {
                try png.write(to: selfTestDirectory.appendingPathComponent("desktop-polish-preview.png"))
            }
            let polishApplyWorks = try await webView.callAsyncJavaScript("""
              document.querySelector('[aria-label="应用 AI 润色"]').click();
              await new Promise(resolve => setTimeout(resolve, 400));
              return document.querySelector('.markdown-editor')?.value.includes('已润色')
                && !!document.querySelector('[aria-label="撤销 AI 润色"]');
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard polishApplyWorks == true else { throw appError("应用润色后文稿或撤销入口无效") }
            let afterPolish = try await requestJSON("/api/sessions/\(sid)")
            guard (afterPolish["markdown_content"] as? String)?.contains(expectedPhoto) == true else { throw appError("润色后截图引用丢失") }
            let afterPolishPhoto = try await request("/api/sessions/\(sid)/photo/\(photoFilename)")
            guard afterPolishPhoto.0 == photoData else { throw appError("润色不应改变截图文件") }
            let polishUndoWorks = try await webView.callAsyncJavaScript("""
              document.querySelector('[aria-label="撤销 AI 润色"]').click();
              await new Promise(resolve => setTimeout(resolve, 400));
              return !document.querySelector('.markdown-editor')?.value.includes('已润色')
                && !document.querySelector('[aria-label="撤销 AI 润色"]');
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard polishUndoWorks == true else { throw appError("撤销润色未恢复原文") }
            let afterUndo = try await requestJSON("/api/sessions/\(sid)")
            guard afterUndo["markdown_content"] as? String == originalPolishText else { throw appError("撤销润色未精确恢复原文") }
            let image = try await webView.takeSnapshot(configuration: nil)
            if let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff), let png = bitmap.representation(using: .png, properties: [:]) {
                try png.write(to: selfTestDirectory.appendingPathComponent("desktop-panel.png"))
            }
            let orbBitmap = orbView.bitmapImageRepForCachingDisplay(in: orbView.bounds)!
            orbView.cacheDisplay(in: orbView.bounds, to: orbBitmap)
            if let png = orbBitmap.representation(using: .png, properties: [:]) { try png.write(to: selfTestDirectory.appendingPathComponent("desktop-orb.png")) }
            print("SELF_TEST_PASSED: native orb, drag clamping, expand/collapse, background service, WebKit bridge, native copy/export, settings, API token and client UI")
            fflush(stdout)
            selfTestFinished = true
            statusTimer?.invalidate()
            await stopBackend()
            NSApp.terminate(nil)
        } catch {
            let detail = (error as NSError).userInfo["WKJavaScriptExceptionMessage"] as? String ?? error.localizedDescription
            failStartup(detail)
        }
    }
}

func appError(_ message: String) -> NSError { NSError(domain: "Parrot", code: 1, userInfo: [NSLocalizedDescriptionKey: message]) }

func jsonLiteral(_ value: Any) -> String {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed])
    return String(data: data, encoding: .utf8)!
}

func unusedPort() throws -> Int {
    let descriptor = socket(AF_INET, SOCK_STREAM, 0)
    guard descriptor >= 0 else { throw appError("无法创建本地服务端口") }
    defer { Darwin.close(descriptor) }
    var address = sockaddr_in()
    address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    address.sin_family = sa_family_t(AF_INET)
    address.sin_addr.s_addr = inet_addr("127.0.0.1")
    let bound = withUnsafePointer(to: &address) { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
    }
    guard bound == 0 else { throw appError("无法分配本地服务端口") }
    var length = socklen_t(MemoryLayout<sockaddr_in>.size)
    let result = withUnsafeMutablePointer(to: &address) { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(descriptor, $0, &length) }
    }
    guard result == 0 else { throw appError("无法读取本地服务端口") }
    return Int(UInt16(bigEndian: address.sin_port))
}

@main
struct ParrotDesktop {
    @MainActor static func main() {
        if let index = CommandLine.arguments.firstIndex(of: "--polish-storage-self-test") {
            do {
                guard CommandLine.arguments.count == index + 3 else { throw polishError("缺少持久化自检参数") }
                try PolishSettingsController.runPersistenceSelfTest(phase: CommandLine.arguments[index + 1],
                                                                   identifier: CommandLine.arguments[index + 2])
            } catch {
                fputs(error.localizedDescription + "\n", stderr)
                exit(1)
            }
            return
        }
        if let index = CommandLine.arguments.firstIndex(of: "--write-iconset"), CommandLine.arguments.count > index + 1 {
            let folder = URL(fileURLWithPath: CommandLine.arguments[index + 1], isDirectory: true)
            try! FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            for size in [16, 32, 128, 256, 512] {
                for scale in [1, 2] {
                    let pixels = size * scale
                    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
                    NSGraphicsContext.saveGraphicsState()
                    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
                    appImage(size: pixels).draw(in: NSRect(x: 0, y: 0, width: pixels, height: pixels))
                    NSGraphicsContext.restoreGraphicsState()
                    let suffix = scale == 2 ? "@2x" : ""
                    let name = "icon_\(size)x\(size)\(suffix).png"
                    try! bitmap.representation(using: .png, properties: [:])!.write(to: folder.appendingPathComponent(name))
                }
            }
            return
        }
        let app = NSApplication.shared
        let delegate = ParrotApp()
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
