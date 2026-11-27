import Cocoa
import Foundation

struct UsbDisk {
    let device: String       // e.g. /dev/disk4
    let rawDevice: String    // e.g. /dev/rdisk4
    let name: String
    let sizeBytes: Int64
    var label: String {
        let gb = Double(sizeBytes) / 1_073_741_824.0
        return String(format: "%@ - %@ (%.2f GB, USB)", device, name, gb)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, URLSessionDownloadDelegate {
    private let releaseAPI = URL(string: "https://api.github.com/repos/LevRa7/LiveUSBAgent-mcp/releases/latest")!
    private let archOptions: [(code: String, label: String)] = [
        ("amd64", "amd64 - Standard 64-bit PC / Laptop (UEFI + BIOS)"),
        ("arm64", "arm64 - ARM64 PC / Mac / Raspberry Pi (UEFI)"),
        ("i386",  "i386  - Old 32-bit PC (BIOS, console TUI)")
    ]

    private var window: NSWindow!
    private var archPopup: NSPopUpButton!
    private var downloadButton: NSButton!
    private var isoField: NSTextField!
    private var browseButton: NSButton!
    private var progressBar: NSProgressIndicator!
    private var hintLabel: NSTextField!

    private var usbPopup: NSPopUpButton!
    private var refreshButton: NSButton!
    private var confirmCheckbox: NSButton!
    private var flashButton: NSButton!
    private var logView: NSTextView!

    private var usbDisks: [UsbDisk] = []
    private var downloadTask: URLSessionDownloadTask?
    private var downloadSession: URLSession?
    private var downloadDestURL: URL?
    private var downloadingName: String = ""
    private var isWriting: Bool = false

    private var downloadsDir: URL {
        FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory())
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildUI()
        autoSelectLatestLocalIso()
        refreshUsbList()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    private func buildUI() {
        let rect = NSRect(x: 0, y: 0, width: 720, height: 610)
        window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "LiveUSBAgent-mcp - Bootable LiveUSB Creator"
        window.center()

        let root = NSView(frame: rect)
        window.contentView = root

        // --- Box 1: ISO Download / Select ---
        let boxIso = NSBox(frame: NSRect(x: 16, y: 405, width: 688, height: 190))
        boxIso.title = "1. LiveUSB ISO Image (Download from GitHub Releases or Select Local .iso)"
        root.addSubview(boxIso)

        let isoContent = boxIso.contentView!
        let lblArch = makeLabel("Target PC:", frame: NSRect(x: 12, y: 132, width: 75, height: 22))
        isoContent.addSubview(lblArch)

        archPopup = NSPopUpButton(frame: NSRect(x: 90, y: 130, width: 375, height: 26), pullsDown: false)
        for item in archOptions { archPopup.addItem(withTitle: item.label) }
        isoContent.addSubview(archPopup)

        downloadButton = NSButton(title: "Download Latest ISO", target: self, action: #selector(onDownloadClicked))
        downloadButton.frame = NSRect(x: 476, y: 128, width: 186, height: 30)
        downloadButton.bezelStyle = .rounded
        isoContent.addSubview(downloadButton)

        let lblIso = makeLabel("ISO File:", frame: NSRect(x: 12, y: 90, width: 75, height: 22))
        isoContent.addSubview(lblIso)

        isoField = NSTextField(frame: NSRect(x: 90, y: 88, width: 468, height: 24))
        isoField.target = self
        isoField.action = #selector(onIsoPathChanged)
        isoContent.addSubview(isoField)

        browseButton = NSButton(title: "Browse...", target: self, action: #selector(onBrowseClicked))
        browseButton.frame = NSRect(x: 566, y: 86, width: 96, height: 28)
        browseButton.bezelStyle = .rounded
        isoContent.addSubview(browseButton)

        progressBar = NSProgressIndicator(frame: NSRect(x: 12, y: 56, width: 650, height: 18))
        progressBar.isIndeterminate = false
        progressBar.minValue = 0
        progressBar.maxValue = 100
        progressBar.doubleValue = 0
        isoContent.addSubview(progressBar)

        hintLabel = makeLabel(
            "Click 'Download Latest ISO' to fetch the official prebuilt image into ~/Downloads, or browse to a local .iso.",
            frame: NSRect(x: 12, y: 12, width: 650, height: 36)
        )
        hintLabel.textColor = .secondaryLabelColor
        hintLabel.maximumNumberOfLines = 2
        isoContent.addSubview(hintLabel)

        // --- Box 2: Target USB Flash Drive ---
        let boxUsb = NSBox(frame: NSRect(x: 16, y: 222, width: 688, height: 170))
        boxUsb.title = "2. Target Removable USB Flash Drive"
        root.addSubview(boxUsb)

        let usbContent = boxUsb.contentView!
        usbPopup = NSPopUpButton(frame: NSRect(x: 12, y: 112, width: 536, height: 26), pullsDown: false)
        usbContent.addSubview(usbPopup)

        refreshButton = NSButton(title: "Refresh USB", target: self, action: #selector(onRefreshUsbClicked))
        refreshButton.frame = NSRect(x: 556, y: 110, width: 106, height: 30)
        refreshButton.bezelStyle = .rounded
        usbContent.addSubview(refreshButton)

        let warnLabel = makeLabel(
            "WARNING: Writing the LiveUSB image will FORMAT and PERMANENTLY ERASE ALL DATA on the selected USB flash drive! Double-check the drive model and size before continuing.",
            frame: NSRect(x: 12, y: 50, width: 650, height: 48)
        )
        warnLabel.textColor = NSColor(calibratedRed: 0.65, green: 0.0, blue: 0.0, alpha: 1.0)
        warnLabel.font = NSFont.boldSystemFont(ofSize: 12)
        warnLabel.maximumNumberOfLines = 3
        usbContent.addSubview(warnLabel)

        confirmCheckbox = NSButton(
            checkboxWithTitle: "I understand that the selected USB drive will be formatted and all existing data will be erased.",
            target: self,
            action: #selector(updateFlashState)
        )
        confirmCheckbox.frame = NSRect(x: 12, y: 16, width: 650, height: 24)
        usbContent.addSubview(confirmCheckbox)

        // --- Action Button ---
        flashButton = NSButton(title: "Write Bootable LiveUSB Flash Drive", target: self, action: #selector(onFlashClicked))
        flashButton.frame = NSRect(x: 16, y: 170, width: 688, height: 38)
        flashButton.bezelStyle = .rounded
        flashButton.font = NSFont.boldSystemFont(ofSize: 13)
        flashButton.isEnabled = false
        root.addSubview(flashButton)

        // --- Log View ---
        let scrollView = NSScrollView(frame: NSRect(x: 16, y: 16, width: 688, height: 142))
        scrollView.hasVerticalScroller = true
        scrollView.borderType = .bezelBorder
        logView = NSTextView(frame: scrollView.bounds)
        logView.isEditable = false
        logView.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        scrollView.documentView = logView
        root.addSubview(scrollView)

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func makeLabel(_ text: String, frame: NSRect) -> NSTextField {
        let lbl = NSTextField(frame: frame)
        lbl.stringValue = text
        lbl.isBezeled = false
        lbl.drawsBackground = false
        lbl.isEditable = false
        lbl.isSelectable = false
        lbl.lineBreakMode = .byWordWrapping
        return lbl
    }

    private func appendLog(_ message: String) {
        let df = DateFormatter()
        df.dateFormat = "HH:mm:ss"
        let line = "[\(df.string(from: Date()))] \(message)\n"
        DispatchQueue.main.async {
            self.logView.textStorage?.append(NSAttributedString(string: line, attributes: [
                .font: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular),
                .foregroundColor: NSColor.textColor
            ]))
            self.logView.scrollToEndOfDocument(nil)
        }
    }

    private func autoSelectLatestLocalIso() {
        guard let items = try? FileManager.default.contentsOfDirectory(
            at: downloadsDir,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else { return }
        let isos = items.filter { $0.lastPathComponent.hasPrefix("LiveUSBAgent-") && $0.pathExtension.lowercased() == "iso" }
            .sorted {
                let d1 = (try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                let d2 = (try? $1.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                return d1 > d2
            }
        if let first = isos.first {
            isoField.stringValue = first.path
        }
    }

    private func runProcess(_ launchPath: String, _ args: [String]) -> (Int32, Data) {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: launchPath)
        task.arguments = args
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        do {
            try task.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            task.waitUntilExit()
            return (task.terminationStatus, data)
        } catch {
            return (-1, Data())
        }
    }

    private func refreshUsbList() {
        usbDisks.removeAll()
        usbPopup.removeAllItems()

        let (rc, data) = runProcess("/usr/sbin/diskutil", ["list", "-plist", "external", "physical"])
        if rc == 0,
           let plist = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any],
           let wholeDisks = plist["WholeDisks"] as? [String] {
            for ident in wholeDisks {
                let (irc, idata) = runProcess("/usr/sbin/diskutil", ["info", "-plist", ident])
                guard irc == 0,
                      let info = try? PropertyListSerialization.propertyList(from: idata, options: [], format: nil) as? [String: Any] else {
                    continue
                }
                let isInternal = (info["Internal"] as? Bool) ?? true
                if isInternal { continue }
                let sizeBytes = (info["TotalSize"] as? NSNumber)?.int64Value ?? 0
                if sizeBytes <= 0 { continue }
                let name = (info["MediaName"] as? String) ?? (info["IORegistryEntryName"] as? String) ?? "External USB Drive"
                usbDisks.append(UsbDisk(
                    device: "/dev/\(ident)",
                    rawDevice: "/dev/r\(ident)",
                    name: name.trimmingCharacters(in: .whitespacesAndNewlines),
                    sizeBytes: sizeBytes
                ))
            }
        }

        if usbDisks.isEmpty {
            usbPopup.addItem(withTitle: "(No removable USB drives detected)")
            appendLog("No external physical USB drives detected. Insert a USB stick and click Refresh USB.")
        } else {
            for d in usbDisks { usbPopup.addItem(withTitle: d.label) }
            appendLog("Detected \(usbDisks.count) external physical USB drive(s).")
        }
        updateFlashState()
    }

    @objc private func onRefreshUsbClicked() {
        refreshUsbList()
    }

    @objc private func onIsoPathChanged() {
        updateFlashState()
    }

    @objc private func updateFlashState() {
        let iso = isoField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let hasIso = !iso.isEmpty && FileManager.default.fileExists(atPath: iso)
        let hasUsb = !usbDisks.isEmpty
        let confirmed = (confirmCheckbox.state == .on)
        flashButton.isEnabled = hasIso && hasUsb && confirmed && (downloadTask == nil) && !isWriting
    }

    @objc private func onBrowseClicked() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = downloadsDir
        if panel.runModal() == .OK, let url = panel.url {
            isoField.stringValue = url.path
            appendLog("Selected ISO: \(url.path)")
            updateFlashState()
        }
    }

    @objc private func onDownloadClicked() {
        if let task = downloadTask {
            task.cancel()
            downloadTask = nil
            appendLog("Cancelling download...")
            return
        }

        let archIdx = max(0, archPopup.indexOfSelectedItem)
        let arch = archOptions[archIdx].code
        appendLog("Checking latest GitHub Release for \(arch) ISO...")
        downloadButton.isEnabled = false

        var req = URLRequest(url: releaseAPI)
        req.setValue("LiveUSBAgent-Creator-macOS/1.0", forHTTPHeaderField: "User-Agent")
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")

        URLSession.shared.dataTask(with: req) { [weak self] data, _, error in
            guard let self = self else { return }
            if let error = error {
                self.appendLog("ERROR querying GitHub Releases: \(error.localizedDescription)")
                DispatchQueue.main.async { self.downloadButton.isEnabled = true }
                return
            }
            guard let data = data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let assets = json["assets"] as? [[String: Any]] else {
                self.appendLog("ERROR: Invalid response from GitHub Releases API.")
                DispatchQueue.main.async { self.downloadButton.isEnabled = true }
                return
            }
            let tag = (json["tag_name"] as? String) ?? "latest"
            guard let match = assets.first(where: {
                let name = ($0["name"] as? String) ?? ""
                return name.hasPrefix("LiveUSBAgent-") && name.lowercased().contains("-\(arch)-") && name.hasSuffix(".iso")
            }),
            let name = match["name"] as? String,
            let urlStr = match["browser_download_url"] as? String,
            let url = URL(string: urlStr) else {
                self.appendLog("ERROR: No \(arch) ISO asset found in release \(tag).")
                DispatchQueue.main.async { self.downloadButton.isEnabled = true }
                return
            }

            let sizeBytes = (match["size"] as? NSNumber)?.doubleValue ?? 0
            self.downloadingName = name
            self.downloadDestURL = self.downloadsDir.appendingPathComponent(name)
            self.appendLog(String(format: "Downloading %@ (%.1f MB) from %@...", name, sizeBytes / 1_048_576.0, tag))

            DispatchQueue.main.async {
                self.progressBar.doubleValue = 0
                self.downloadButton.title = "Cancel Download"
                self.downloadButton.isEnabled = true
                self.browseButton.isEnabled = false
                self.archPopup.isEnabled = false
                let cfg = URLSessionConfiguration.default
                self.downloadSession = URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
                var dlReq = URLRequest(url: url)
                dlReq.setValue("LiveUSBAgent-Creator-macOS/1.0", forHTTPHeaderField: "User-Agent")
                self.downloadTask = self.downloadSession?.downloadTask(with: dlReq)
                self.downloadTask?.resume()
                self.updateFlashState()
            }
        }.resume()
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64, totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        guard totalBytesExpectedToWrite > 0 else { return }
        let pct = Double(totalBytesWritten) * 100.0 / Double(totalBytesExpectedToWrite)
        let mbDone = Double(totalBytesWritten) / 1_048_576.0
        let mbTotal = Double(totalBytesExpectedToWrite) / 1_048_576.0
        DispatchQueue.main.async {
            self.progressBar.doubleValue = pct
            self.hintLabel.stringValue = String(format: "Downloading %@: %.1f MB / %.1f MB (%.0f%%)", self.downloadingName, mbDone, mbTotal, pct)
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        guard let dest = downloadDestURL else { return }
        do {
            if FileManager.default.fileExists(atPath: dest.path) {
                try FileManager.default.removeItem(at: dest)
            }
            try FileManager.default.moveItem(at: location, to: dest)
            DispatchQueue.main.async {
                self.progressBar.doubleValue = 100
                self.isoField.stringValue = dest.path
                self.hintLabel.stringValue = "Downloaded: \(dest.path)"
                self.appendLog("Download complete: \(dest.path)")
            }
        } catch {
            self.appendLog("ERROR saving downloaded ISO: \(error.localizedDescription)")
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        DispatchQueue.main.async {
            self.downloadTask = nil
            self.downloadButton.title = "Download Latest ISO"
            self.downloadButton.isEnabled = true
            self.browseButton.isEnabled = true
            self.archPopup.isEnabled = true
            if let error = error {
                let nsErr = error as NSError
                if nsErr.code == NSURLErrorCancelled {
                    self.appendLog("Download cancelled.")
                    self.hintLabel.stringValue = "Download cancelled."
                } else {
                    self.appendLog("Download failed: \(error.localizedDescription)")
                    self.hintLabel.stringValue = "Download failed. Check your connection or select a local .iso."
                }
                self.progressBar.doubleValue = 0
            }
            self.updateFlashState()
        }
    }

    private func shellQuote(_ s: String) -> String {
        return "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    private func appleScriptEscape(_ s: String) -> String {
        return s.replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "\"", with: "\\\"")
    }

    @objc private func onFlashClicked() {
        let idx = usbPopup.indexOfSelectedItem
        guard idx >= 0 && idx < usbDisks.count else { return }
        let disk = usbDisks[idx]
        let iso = isoField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard FileManager.default.fileExists(atPath: iso) else { return }

        let gb = Double(disk.sizeBytes) / 1_073_741_824.0
        let alert = NSAlert()
        alert.messageText = "Confirm USB Format & Erase"
        alert.informativeText = String(
            format: "FINAL WARNING:\n\nALL DATA on %@ (%@, %.2f GB) will be FORMATTED and PERMANENTLY ERASED!\n\nISO: %@\n\nProceed with writing the bootable LiveUSB?",
            disk.device, disk.name, gb, iso
        )
        alert.alertStyle = .critical
        alert.addButton(withTitle: "Erase & Write")
        alert.addButton(withTitle: "Cancel")
        if alert.runModal() != .alertFirstButtonReturn {
            appendLog("USB write cancelled by user.")
            return
        }

        isWriting = true
        updateFlashState()
        appendLog("Writing \(iso) to \(disk.rawDevice) (macOS administrator password prompt will appear)...")

        let shellCmd = "/usr/sbin/diskutil unmountDisk \(shellQuote(disk.device)) && /bin/dd if=\(shellQuote(iso)) of=\(shellQuote(disk.rawDevice)) bs=4m && /usr/sbin/diskutil eject \(shellQuote(disk.device))"
        let script = "do shell script \"\(appleScriptEscape(shellCmd))\" with administrator privileges"

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let task = Process()
            task.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
            task.arguments = ["-e", script]
            let errPipe = Pipe()
            task.standardError = errPipe
            do {
                try task.run()
                let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
                task.waitUntilExit()
                let rc = task.terminationStatus
                let errText = String(data: errData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                DispatchQueue.main.async {
                    self.isWriting = false
                    self.updateFlashState()
                    if rc == 0 {
                        self.appendLog("SUCCESS: LiveUSB written to \(disk.device) and ejected safely.")
                        let done = NSAlert()
                        done.messageText = "LiveUSB Ready"
                        done.informativeText = "Successfully wrote LiveUSB to \(disk.device) and ejected it safely."
                        done.runModal()
                    } else {
                        self.appendLog("ERROR (exit \(rc)): \(errText)")
                        let fail = NSAlert()
                        fail.messageText = "Write Failed"
                        fail.informativeText = errText.isEmpty ? "osascript exited with code \(rc)." : errText
                        fail.alertStyle = .warning
                        fail.runModal()
                    }
                }
            } catch {
                DispatchQueue.main.async {
                    self.isWriting = false
                    self.updateFlashState()
                    self.appendLog("ERROR launching osascript: \(error.localizedDescription)")
                }
            }
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
