"""Build-time artwork and fixed-command launcher for the macOS app bundle."""

# The app owns the Python child for its entire lifetime. Never accept a script,
# executable or state path from launcher arguments after granting OS access.
MONITOR_SOURCE = r'''
func runMonitor() -> Never {
    let args = CommandLine.arguments
    guard args.count == 3, ["tick", "scan"].contains(args[2]),
          let cli = Bundle.main.object(forInfoDictionaryKey: "ASGMonitorCLI") as? String,
          let state = Bundle.main.object(forInfoDictionaryKey: "ASGMonitorState") as? String else {
        fputs("Expected --monitor tick|scan with an installed monitor configuration.\n", stderr)
        exit(64)
    }
    let worker = Process()
    worker.executableURL = URL(fileURLWithPath: cli)
    worker.arguments = ["--state", state, "monitor", args[2], "--notify"]
    worker.terminationHandler = { process in
        exit(process.terminationReason == .exit ? process.terminationStatus : 128 + process.terminationStatus)
    }
    // SIGINT lets Python unwind scanner finally blocks and reap its native du.
    signal(SIGTERM, SIG_IGN)
    signal(SIGINT, SIG_IGN)
    let signals = [SIGTERM, SIGINT].map { number -> DispatchSourceSignal in
        let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
        source.setEventHandler { if worker.isRunning { worker.interrupt() } }
        source.resume()
        return source
    }
    do { try worker.run() }
    catch { fputs("Monitor launch failed: \(error)\n", stderr); exit(74) }
    withExtendedLifetime(signals) { dispatchMain() }
}
if CommandLine.arguments.dropFirst().first == "--monitor" { runMonitor() }
'''

# Original artwork, rendered locally with Apple's frameworks. No downloaded
# icon, network request, or runtime icon-writing command in the permission app.
ICON_SOURCE = r'''
import AppKit
import Foundation
let directory = URL(fileURLWithPath: CommandLine.arguments[1])
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let transform = NSAffineTransform()
        transform.scale(by: CGFloat(pixels) / 1024)
        transform.concat()
        NSColor(calibratedRed: 0.05, green: 0.29, blue: 0.25, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 40, y: 40, width: 944, height: 944), xRadius: 210, yRadius: 210).fill()
        NSColor(calibratedRed: 0.88, green: 0.98, blue: 0.91, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 220, y: 340, width: 584, height: 370), xRadius: 65, yRadius: 65).fill()
        NSColor(calibratedRed: 0.05, green: 0.29, blue: 0.25, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 260, y: 385, width: 504, height: 90), xRadius: 30, yRadius: 30).fill()
        NSColor(calibratedRed: 0.41, green: 0.84, blue: 0.56, alpha: 1).setFill()
        NSBezierPath(ovalIn: NSRect(x: 657, y: 408, width: 42, height: 42)).fill()
        let check = NSBezierPath()
        check.move(to: NSPoint(x: 403, y: 577))
        check.line(to: NSPoint(x: 480, y: 511))
        check.line(to: NSPoint(x: 631, y: 640))
        check.lineWidth = 43
        check.lineCapStyle = .round
        check.lineJoinStyle = .round
        check.stroke()
        image.unlockFocus()
        guard let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let data = bitmap.representation(using: .png, properties: [:]) else { exit(74) }
        let suffix = scale == 2 ? "@2x" : ""
        try data.write(to: directory.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
'''
