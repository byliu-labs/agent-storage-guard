# Private alert setup on macOS

Run these commands from a checkout containing this PR's code. Until it merges,
the default branch does not contain these setup commands. Python 3.11+, uv and
Apple's command-line tools (`swiftc`) are required for the installed native helper.
No hosted relay or paid messaging service is provisioned.

## Install and save Feishu settings

```sh
uv tool install --reinstall .
agent-storage-guard monitor setup --provider feishu
```

Setup asks for **both the webhook URL and signing secret**, with echo disabled.
In the target group, open Settings → Bots → your custom bot → Security settings →
Signature Verification, copy its secret, and save the bot settings. Obtain the
webhook from that same bot. See the [official guide](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot).
Lark uses the same command with its own bot URL. Optional keyword/IP restrictions
must also permit the sender and redacted message.

The two values go directly into a new Keychain item: service `asg-feishu-feishu`,
account `agent-storage-guard`. Setup creates private monitor configuration if needed
and stores only a credential reference in it. No manual JSON editing, credential
file, secret shell variable or secret-bearing command argument is needed. The
Keychain must already be accessible; setup does not unlock it or alter existing ACLs.

Choose roots on first setup with repeatable `--root` arguments, for example the
synthetic `--root /Volumes/CIExample/workspaces`. Omit them for the documented defaults.
Existing roots/destinations are preserved; `--root` on an existing monitor is refused.
Fresh setup enables only the selected provider. Adding to an already active monitor
takes effect on its next notifying invocation.

Existing destinations and Keychain items are never overwritten. Use a new
`--destination secondary` to add a separate item. Use a new ID when changing
recipients so they do not inherit another recipient's cooldown. If interrupted
after Keychain storage but before config publication, the item stays preserved;
choose a new ID or inspect that exact item manually in Keychain Access.

## Check, test the installed identity, then activate

```sh
agent-storage-guard monitor delivery-check
agent-storage-guard monitor install
agent-storage-guard monitor test-send --destination feishu --installed
```

The check is offline. Installation prepares the native helper and rollback manifest
without activating jobs. The explicit test sends clearly marked synthetic
measurements through that helper. Confirm receipt in your group: `accepted` alone
does not establish receipt. After confirmation and reviewing roots/schedules:

```sh
agent-storage-guard monitor scan
agent-storage-guard monitor report --open
agent-storage-guard monitor install --activate
```

An unchanged signed helper is reused across staging/activation, preserving its
identity. Updated native code or manifest requires a rebuild and may require OS reapproval.
Only monitor jobs are affected. Preserve the returned manifest for
`monitor rollback MANIFEST`. Full Disk Access is needed only for folders that
require it, not messaging itself.

Keychain access is non-interactive. If blocked, unlock the existing Keychain and
review the specific item's Access Control in Keychain Access. Authorize only the
actual installed runtime, not all applications. Python retrieves the item; the
native helper is the responsible parent for scheduled execution. Interactive
success alone is not scheduled-access proof; a locked Keychain remains a gate.
[Apple's access-control instructions](https://support.apple.com/en-gb/guide/mac-help/kychn002/26/mac/26).

## Add iMessage

```sh
agent-storage-guard monitor setup --provider imessage
agent-storage-guard monitor delivery-check
agent-storage-guard monitor install
agent-storage-guard monitor test-send --destination imessage --installed
```

Setup privately asks for one recipient phone number/email and stores it only in
Keychain (`asg-imessage-imessage`, account `agent-storage-guard`). No Apple Account
password or API key is requested. Delivery reuses Feishu's redaction, health,
restart/cooldown policy and existing SQLite store.

Before the test, sign into **Messages → Settings → iMessage** on the monitored Mac
and manually verify a blue-bubble conversation with the recipient. The adapter
selects a connected iMessage account and implements no SMS/RCS fallback. Sign-in
and account/session changes remain owner-operated. [Apple's Messages setup](https://support.apple.com/guide/messages/set-up-messages-ichte16154fb/mac).

During the explicit installed test, approve the request to let the installed
sender control Messages. Review **System Settings → Privacy & Security → Automation**.
Approving Terminal during a direct CLI test does not prove the installed sender has
permission. This send-only integration does not use Accessibility, Screen Recording,
Messages database access, an inbound server or Full Disk Access.
[Apple's Automation instructions](https://support.apple.com/guide/mac-help/mchl108e1718/mac).

Installed tests now show a safe error code and a specific next action on failure.
For `timeout`, open Messages in the logged-in desktop session and finish any pending
Automation prompt. Check the recipient device before retrying: a timeout leaves
receipt uncertain. Use `monitor delivery-check` to review saved redacted health.
A timeout alone does not prove permission denial.

The first test can time out while approval is pending; approve and rerun it.
Native execution is bounded to ten seconds and its child is reaped. Permission,
service and other failures appear as redacted gates in `delivery-check`. Installed
tests reject a helper bound to another state rather than using its recipient.
Confirm receipt on the recipient device; scripting acceptance is not network delivery.

The sender's normal iMessage email/phone and potentially profile identity are visible
to recipients. Allowlisted content does not make the transport anonymous. No hosted
relay or per-message billing service is added; normal account/network requirements apply.

A signed-in desktop Mac needs fewer credentials than Feishu, but needs OS approval.
A logged-out, freshly rebooted or SSH-only host without a working graphical Messages
session must not rely on this as its sole alert channel. Activate explicitly after
confirmation if not already active. Reinstall when source, paths or schedules change.

## Keep setup outside Git

Defaults keep config, inventory, reports and history under `~/.asg`, outside the
checkout. Setup rejects state inside Git checkouts/worktrees before accepting private
input. Secret/recipient values go only to Keychain and transient memory/private pipes,
never receipts. Config holds generic IDs/references and a non-identifying label.

Do not paste secrets into chat or commit a `.env`, personal config, plist, screenshot,
Keychain export, SQLite file or local report. Reports contain private paths. Examples
and tests use synthetic data. Do not attach local state to the PR. See
[outbound fields and provider boundaries](remote-alerts.md).

These opt-in setup/test commands do not authorize cleanup, database changes,
process termination or inventory uploads.

## Identify each monitored computer

Set a short source label before activating each computer:

```sh
agent-storage-guard monitor configure --label "Laptop"
# On the other computer:
agent-storage-guard monitor configure --label "Build Mac"
```

Changing the label sends nothing and preserves roots, destinations, credentials,
and history. Choose a label you are comfortable sharing with the configured
recipients. Hostnames and account names are never copied into it automatically.
A package installation alone is not an active monitor: each computer also needs
its own initialized roots, destination settings, activated jobs, and fresh samples.
Review `monitor status` and the local installation manifest on each host.

## Readable remote alerts

Feishu and iMessage identify the source and reason, for example:

```text
Laptop — URGENT
Reason: Low free space

Free space: 22.0 GiB
Disk use change: -10.5 GiB over 24.0 hours
Memory pressure: Warning
Scan coverage: Partial — 7/9 roots complete; 2 permission gaps, 0 deadline gaps, 0 other/unknown gaps
Measured: 2026-01-01 00:00 UTC

Next step: Pause large builds; review recent growers in the private local report and preserve unfinished work before moving reviewed finished outputs.
```

Disk use change comes from comparable capacity samples on the same volume within
24 hours; its sign distinguishes increased usage from reclaimed space. Folder
growth has its own interval and is shown on folder-growth alerts, rather than
showing a misleading unavailable folder metric on reserve or memory alerts.
A first sample, changed volume, or stale capacity cannot establish a change.

Coverage counts refer to configured roots, not every nested row or the whole
computer. Permission and deadline causes remain distinct; one root may have both.
Actual errors, paths, per-folder timestamps and retained baselines stay in the
private local report. Failed reads never become zero bytes or complete coverage.

For permission gaps on macOS, review System Settings → Privacy & Security →
Full Disk Access for the installed **Agent Storage Guard** app. If access is off,
turn it back on and authenticate. A rebuilt native helper can require reapproval;
updating Python-only code does not require rebuilding the helper. Do not grant
blanket access to a shared Python interpreter. After approval, rerun the scheduled
scan through the installed helper and confirm its real root measurements.

After updating, reinstall the CLI. Run `monitor install` when helper code, paths,
or schedules changed; unchanged helpers are reused. Existing Keychain settings
remain in place. Explicit test sends are optional and must be requested deliberately;
normal tests use synthetic data and local HTTP servers.
