"""Native local notifications; successful enqueue does not prove visible delivery.

The optional macOS helper uses Apple's installed frameworks and is compiled at
installation. Its own app identity replaces Script Editor and its actions open
only a local report or Activity Monitor. No dependency download is performed.
"""
from __future__ import annotations

import os
import hashlib
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import uuid

from .app_identity import ICON_SOURCE, MONITOR_SOURCE

BUNDLE_ID = "io.github.byliu-labs.agent-storage-guard"
SCRIPT = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
SWIFT_SOURCE = r'''
import AppKit
import Foundation
import UserNotifications

// MONITOR_LAUNCHER

final class AlertDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationWillFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        runRequest()
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter,
         willPresent notification: UNNotification,
         withCompletionHandler handler: @escaping (UNNotificationPresentationOptions) -> Void) {
        handler([.banner, .list])
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter,
         didReceive response: UNNotificationResponse,
         withCompletionHandler handler: @escaping () -> Void) {
        DispatchQueue.main.async {
            if response.actionIdentifier == "MEMORY" {
                NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/Utilities/Activity Monitor.app"))
            } else if let path = Bundle.main.object(forInfoDictionaryKey: "ASGReportPath") as? String {
                NSWorkspace.shared.open(URL(fileURLWithPath: path))
            }
            handler()
            finish("Opened notification action.")
        }
    }
}
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let center = UNUserNotificationCenter.current()
let delegate = AlertDelegate()
application.delegate = delegate
center.delegate = delegate
func finish(_ text: String, _ code: Int32 = 0) {
    print(text)
    fflush(stdout)
    // A real AppKit lifecycle also handles actions when Launch Services
    // relaunches this helper after its original sending process has exited.
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { exit(code) }
}
func runRequest() {
if CommandLine.arguments.contains("--diagnose") {
    center.getNotificationSettings { settings in
        center.getDeliveredNotifications { delivered in
            let names: [Int: String] = [0: "notDetermined", 1: "denied", 2: "authorized", 3: "provisional"]
            let payload: [String: Any] = ["bundle_id": Bundle.main.bundleIdentifier ?? "unknown",
                "authorization": names[settings.authorizationStatus.rawValue] ?? "unknown",
                "authorization_status": settings.authorizationStatus.rawValue,
                "alerts_enabled": settings.alertSetting == .enabled,
                "notification_center_enabled": settings.notificationCenterSetting == .enabled,
                "alert_style": settings.alertStyle.rawValue,
                "delivered_count": delivered.count]
            if let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]),
               let output = String(data: data, encoding: .utf8) { finish(output) }
            else { finish("Cannot serialize notification diagnostics", 74) }
        }
    }
} else if CommandLine.arguments.contains("--self-test") {
    center.getNotificationSettings { settings in
        finish("bundle=\(Bundle.main.bundleIdentifier ?? "unknown") authorizationStatus=\(settings.authorizationStatus.rawValue)")
    }
} else if CommandLine.arguments.count >= 5 {
    let args = CommandLine.arguments
    let report = UNNotificationAction(identifier: "REPORT", title: "Open storage overview", options: [.foreground])
    let memory = UNNotificationAction(identifier: "MEMORY", title: "Open Activity Monitor", options: [.foreground])
    center.setNotificationCategories([
        UNNotificationCategory(identifier: "REPORT", actions: [report], intentIdentifiers: []),
        UNNotificationCategory(identifier: "MEMORY", actions: [memory, report], intentIdentifiers: [])])
    center.requestAuthorization(options: [.alert]) { granted, error in
        guard granted && error == nil else {
            finish("Notifications are unavailable. Open System Settings > Notifications > Agent Storage Guard and allow notifications. \(error?.localizedDescription ?? "Permission was not granted.")", 77)
            return
        }
        let content = UNMutableNotificationContent()
        content.title = args[2]
        content.body = args[3]
        content.categoryIdentifier = args[4]
        content.threadIdentifier = "storage-guard"
        let request = UNNotificationRequest(identifier: args[1], content: content, trigger: nil)
        center.add(request) { error in
            if let error = error {
                finish("Notification enqueue failed: \(error.localizedDescription)", 74)
            } else {
                finish("Notification accepted by macOS; display depends on notification settings and Focus.")
            }
        }
    }
} else {
    // Launch Services may relaunch the application for a notification action.
    DispatchQueue.main.asyncAfter(deadline: .now() + 10) { finish("No notification action received.") }
}
}
DispatchQueue.main.asyncAfter(deadline: .now() + 25) {
    finish("Notification service timed out. If permission is pending, allow Agent Storage Guard in System Settings > Notifications, then retry.", 75)
}
application.run()
'''


def _reusable_app(app: Path, manifest: dict) -> bool:
    paths = [app, app / "Contents", app / "Contents/Info.plist", app / "Contents/MacOS",
             app / "Contents/MacOS/Agent Storage Guard"]
    try:
        if any(path.is_symlink() or path.stat().st_uid != os.getuid() for path in paths):
            return False
        if not paths[-1].is_file() or not os.access(paths[-1], os.X_OK):
            return False
        if plistlib.loads(paths[2].read_bytes()) != manifest:
            return False
        return subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, ValueError, plistlib.InvalidFileException, subprocess.SubprocessError):
        return False


def prepare_app(directory: Path, report_path: Path, executable: Path | None = None,
                state: Path | None = None) -> Path:
    """Build a private, ad-hoc signed helper. Preserve a replaced bundle for rollback."""
    if sys.platform != "darwin":
        raise RuntimeError("native app helper requires macOS")
    compiler = shutil.which("swiftc")
    if compiler is None:
        raise RuntimeError("swiftc unavailable; install Apple command line tools or use the AppleScript fallback")
    directory = Path(directory).expanduser().absolute()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise RuntimeError("notification app directory must be owned and must not be a symlink")
    app = directory / "Agent Storage Guard.app"
    source_text = SWIFT_SOURCE.replace("// MONITOR_LAUNCHER", MONITOR_SOURCE)
    manifest = {"CFBundleIdentifier": BUNDLE_ID, "CFBundleName": "Agent Storage Guard",
                "CFBundleDisplayName": "Agent Storage Guard", "CFBundleExecutable": "Agent Storage Guard",
                "CFBundlePackageType": "APPL", "CFBundleVersion": "1", "LSUIElement": True,
                "NSHighResolutionCapable": True, "ASGReportPath": str(report_path.absolute()),
                "CFBundleIconFile": "StorageGuard.icns",
                "NSAppleEventsUsageDescription": "Send explicitly configured storage alerts through Messages."}
    if executable is not None and state is not None:
        manifest.update(ASGMonitorCLI=str(executable.absolute()), ASGMonitorState=str(state.absolute()))
    digest = hashlib.sha256(source_text.encode() + ICON_SOURCE.encode() + plistlib.dumps(manifest)).hexdigest()
    manifest["ASGBuildDigest"] = digest
    if _reusable_app(app, manifest):
        return app
    with tempfile.TemporaryDirectory(prefix=".notification-build-", dir=directory) as temporary:
        root = Path(temporary)
        staged = root / app.name
        contents = staged / "Contents"
        binary = contents / "MacOS/Agent Storage Guard"
        binary.parent.mkdir(parents=True, mode=0o700)
        source = root / "Alert.swift"
        source.write_text(source_text)
        subprocess.run([compiler, "-O", str(source), "-o", str(binary)],
                       check=True, capture_output=True, text=True, timeout=120)
        resources = contents / "Resources"
        resources.mkdir(mode=0o700)
        iconset = root / "StorageGuard.iconset"
        iconset.mkdir()
        artwork = root / "Icon.swift"
        artwork.write_text(ICON_SOURCE)
        subprocess.run([shutil.which("swift") or "swift", str(artwork), str(iconset)],
                       check=True, capture_output=True, text=True, timeout=120)
        subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(resources / "StorageGuard.icns")],
                       check=True, capture_output=True, text=True, timeout=20)
        (contents / "Info.plist").write_bytes(plistlib.dumps(manifest))
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID, str(staged)],
                       check=True, capture_output=True, text=True, timeout=20)
        if app.exists() or app.is_symlink():
            app.rename(directory / (app.name + ".previous-" + uuid.uuid4().hex))
        staged.rename(app)
    register = Path("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister")
    subprocess.run([str(register), "-f", str(app)], check=True, capture_output=True, text=True, timeout=20)
    return app


def command(alert: dict, app: Path | None = None) -> list[str]:
    if app:
        category = "MEMORY" if alert.get("key") == "memory" else "REPORT"
        return [str(Path(app) / "Contents/MacOS/Agent Storage Guard"), str(alert.get("key", "storage")),
                alert["title"], alert["message"], category]
    if sys.platform == "darwin":
        return ["/usr/bin/osascript", "-e", SCRIPT, alert["title"], alert["message"]]
    executable = shutil.which("notify-send")
    if executable:
        return [executable, "--app-name=Agent Storage Guard", "--", alert["title"], alert["message"]]
    raise RuntimeError("no native notification sender available; read monitor status instead")


def notify(alert: dict, app: Path | None = None, report_path: Path | None = None) -> None:
    """Raise on rejected permission, failed enqueue or timeout; caller commits delivery."""
    del report_path  # The app's signed manifest fixes the local report destination.
    result = subprocess.run(command(alert, app), capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"native notification failed ({result.returncode}): {result.stderr or result.stdout}")


def diagnose(app: Path) -> dict:
    """Inspect our own native notification settings without requesting permission."""
    import json
    result = subprocess.run([str(Path(app) / "Contents/MacOS/Agent Storage Guard"), "--diagnose"],
                            check=True, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)
