import AppKit
import CoreGraphics
import Darwin
import Foundation
import UniformTypeIdentifiers
import WebKit

private let accent = NSColor(srgbRed: 0.26, green: 0.39, blue: 0.92, alpha: 1)

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
    var activity: NSObjectProtocol?
    var startupActivity: NSObjectProtocol?
    var lastDesktopState: Data?
    let localSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.connectionProxyDictionary = [:]
        configuration.timeoutIntervalForRequest = 2
        configuration.timeoutIntervalForResource = 5
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
                const timer = setTimeout(() => {waiting.delete(id);reject(new Error('操作超时，请重试'));}, 300000);
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
        if workspace.isVisible { workspace.orderOut(nil) }
        else { showWorkspace() }
        writeDesktopState()
    }

    func showWorkspace() {
        positionWorkspace()
        NSApp.activate(ignoringOtherApps: true)
        workspace.makeKeyAndOrderFront(nil)
        if !serverReady { workspace.title = "Parrot · 正在启动…" }
        else { workspace.title = "Parrot" }
        writeDesktopState()
    }

    @objc func openCaptureSettings() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc func openSettings() {
        showWorkspace()
        if webView.isLoading || !serverReady { pendingSettings = true }
        else { webView.evaluateJavaScript("window.dispatchEvent(new Event('parrot-open-settings'))") }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { sender.orderOut(nil); writeDesktopState(); return false }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showWorkspace(); return true }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.frameInfo.securityOrigin.host == "127.0.0.1",
              message.frameInfo.securityOrigin.port == port,
              let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        let id = body["id"] as? String
        switch action {
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
            // Only a direct Start action requests access. Do not prompt on launch
            // or from the once-per-second status poll.
            let granted = selfTesting || CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess()
            reply(id, value: ["granted": granted])
        case "openCaptureSettings":
            if !selfTesting { openCaptureSettings() }
            reply(id, value: ["opened": true])
        case "collapse": workspace.orderOut(nil); writeDesktopState()
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
        case "export":
            guard let sessionID = body["sessionId"] as? String else { reply(id, error: "记录不存在"); return }
            Task { await exportMarkdown(sessionID, title: body["title"] as? String ?? "学习记录", content: body["content"] as? String, replyID: id) }
        default: reply(id, error: "不支持的客户端操作")
        }
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
            if selfTesting {
                let destination = selfTestDirectory.appendingPathComponent("export-test.md")
                try data.write(to: destination, options: .atomic)
                reply(replyID, value: ["saved": true])
                return
            }
            let panel = NSSavePanel()
            panel.title = "导出 Markdown"
            panel.nameFieldStringValue = title.replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "\\", with: "_") + ".md"
            panel.allowedContentTypes = [UTType(filenameExtension: "md") ?? .plainText]
            panel.canCreateDirectories = true
            panel.beginSheetModal(for: workspace) { [weak self] result in
                guard let self = self else { return }
                guard result == .OK, let destination = panel.url else { self.reply(replyID, value: ["saved": false]); return }
                do { try data.write(to: destination, options: .atomic); self.reply(replyID, value: ["saved": true]) }
                catch { self.reply(replyID, error: error.localizedDescription) }
            }
        } catch { reply(replyID, error: error.localizedDescription) }
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

    func request(_ path: String, method: String = "GET") async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 2
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
            let exported = try String(contentsOf: selfTestDirectory.appendingPathComponent("export-test.md"), encoding: .utf8)
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
              document.querySelector('.markdown-actions button:last-child').click();
              const exportButton = Array.from(document.querySelectorAll('button')).find(node => node.textContent.includes('导出'));
              exportButton.click();
              await new Promise(resolve => setTimeout(resolve, 1500));
              return document.querySelector('.markdown-editor')?.value === content
                && document.querySelector('[aria-label="保存 Markdown"]')?.textContent.includes('已保存');
            """, arguments: ["content": editedMarkdown], in: nil, contentWorld: .page) as? Bool
            guard markdownEditWorks == true else { throw appError("Markdown 编辑或自动保存失败") }
            guard copiedForTest == editedMarkdown else { throw appError("复制没有使用编辑后的 Markdown") }
            let editedExport = try String(contentsOf: selfTestDirectory.appendingPathComponent("export-test.md"), encoding: .utf8)
            guard editedExport == editedMarkdown else { throw appError("导出没有使用编辑后的 Markdown") }
            let (editedData, _) = try await request("/api/sessions/\(sid)")
            let editedSession = try JSONSerialization.jsonObject(with: editedData) as? [String: Any]
            guard editedSession?["markdown_content"] as? String == editedMarkdown else { throw appError("Markdown 未持久保存") }
            guard editedSession?["title"] as? String == "直接编辑验证" else { throw appError("记录名称未跟随 Markdown 标题") }
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
            openSettings()
            try await Task.sleep(nanoseconds: 300_000_000)
            let settingsVisible = try await webView.evaluateJavaScript("!!document.querySelector('.ant-modal-content')") as? Bool
            guard settingsVisible == true else { throw appError("设置面板未打开") }
            let downloadedModelStatus = try await webView.callAsyncJavaScript("""
              const originalFetch = window.fetch;
              window.fetch = async (input, options) => {
                const response = await originalFetch(input, options);
                if (new URL(String(input), location.href).pathname !== '/api/status') return response;
                const body = await response.json();
                body.model = {status:'idle', message:'', engine:null, model:null};
                body.model_cache.downloaded[body.model_cache.auto_engine]['large-v3'] = true;
                return new Response(JSON.stringify(body), {headers:{'Content-Type':'application/json'}});
              };
              try {
                await new Promise(resolve => setTimeout(resolve, 1800));
                const section = document.querySelector('.model-preparation');
                return !!section && section.textContent.includes('模型已下载') && !section.querySelector('button');
              } finally {
                window.fetch = originalFetch;
              }
            """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            guard downloadedModelStatus == true else { throw appError("已下载模型应显示已下载状态，并隐藏下载或加载按钮") }
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
