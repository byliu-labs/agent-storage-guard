# Feishu, Lark and iMessage alerts

This implementation of [issue #6](https://github.com/byliu-labs/agent-storage-guard/issues/6)
adds Feishu/Lark group bots and macOS iMessage alongside native notifications.
Slack and WhatsApp remain required and unimplemented. There is no automatic cleanup
or change to existing CI commands. Start with [private setup](setup.md) for hidden
credential capture, installed-identity tests and explicit activation.

## Feishu/Lark prerequisites and advanced configuration

Create a dedicated test group and add a custom bot using the
[Feishu custom-bot guide](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)
or [Lark custom-bot guide](https://open.larksuite.com/document/client-docs/bot-v3/add-custom-bot).
The webhook selects the group; this adapter cannot send arbitrary direct messages.
Keep the webhook and optional signing secret private. Enable signing on the bot
and supply `signing_secret` if that policy is enabled. The adapter signs the current
timestamp using the provider's HMAC-SHA256 scheme. Keep the Mac's clock correct.
Tenant policy, keyword restrictions and IP restrictions can independently reject
a message. Choose restrictions compatible with the redacted payload; do not add
private keywords to messages just to satisfy a bot policy.

This route uses an existing group bot and outbound HTTPS, with no hosted relay or
new paid infrastructure. Check the tenant's current plan and bot policy before
activation; this package does not provision services or promise a provider price.
The existing bot and its group must remain available while the monitor is unattended.

Initialize custom roots as shown in the [CI-host recipe](ci-host-monitoring.md).
Edit the private `STATE/monitor/config.json`, retaining its existing fields:

```json
{
  "operator_label": "CI monitor",
  "destinations": [
    {"id": "operations", "provider": "feishu", "settings_keychain": "asg-feishu-operations"}
  ]
}
```

The example above is a configuration fragment, not a replacement for the entire
configuration. The label is included remotely: use a non-identifying name, without
automatically collecting hostnames, account names or private paths. Set it with
`monitor configure --label "Laptop"` (use another short label on each host). IDs and credential references are
local. Use a new destination ID when changing the recipient behind a credential
reference, so the new recipient does not inherit the old recipient's cooldown.

For scheduled macOS operation, use Keychain Access to create a generic-password
item with service/name `asg-feishu-operations`, account `agent-storage-guard`, and
password containing the provider settings JSON. The shape below is synthetic:

```json
{
  "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/SYNTHETIC-EXAMPLE",
  "signing_secret": "SYNTHETIC-EXAMPLE"
}
```

For Lark, use the bot's `https://open.larksuite.com/open-apis/bot/v2/hook/...`
URL with the same `feishu` provider. Omit `signing_secret` only when the bot does
not require signing. Only these two HTTPS hosts and bot endpoint paths are accepted;
redirects are refused. Credential JSON accepts only the fields above.

`monitor setup --provider feishu` automates Keychain item creation and the config
reference, requiring signing. The manual form remains available for existing
credential mechanisms; guided setup avoids secrets in shell commands.

Keychain retrieval is restricted to the named service and fixed account. It never
unlocks a keychain or opens a permission dialog. An inaccessible, locked or
unapproved item produces a redacted gate. The owner must authorize access for the
actual installed runtime as appropriate; this package does not change Keychain
ACLs. A login Keychain that locks on logout can prevent unattended sends. Validate
under the scheduled identity and intended session state before relying on alerts.
Keychain APIs are macOS-only; no Keychain lookup is attempted on other platforms.

For an interactive process or an existing scheduler that securely supplies its
environment, replace `settings_keychain` with `settings_env`, for example
`"settings_env": "ASG_FEISHU_OPERATIONS"`. That variable must contain the settings
JSON. Supply it through your existing credential mechanism; do not paste values
into shell history, plists, reports, command arguments or source control. An entry
requires exactly one credential reference. Interactive shell variables are not
automatically inherited by launchd, so an interactive check is not scheduled-access
proof. Credentials and recipient configuration are never copied into SQLite.

## Check and deliberately send

```sh
agent-storage-guard --state /Volumes/CIExample/guard-state monitor delivery-check
agent-storage-guard --state /Volumes/CIExample/guard-state monitor test-send --destination operations
```

`delivery-check` validates configuration and credential access offline. It does not
contact the bot or request notification/Automation permissions. It shows each
destination's configuration, last acceptance/error, retry time and an actionable
gate. A nonzero exit means a configuration gate or last failed delivery remains.
`configuration: ready` proves neither provider access nor message receipt.

Run `test-send` only after authorizing the selected dedicated test group. This is
the only command here that deliberately sends a test message. It sends fixed
synthetic low-space, growth and critical-pressure measurements without probing the
host. Its receipt distinguishes `acceptance: accepted` from
`receipt: not_observed`. Independently open the dedicated group and confirm receipt;
provider acceptance alone cannot establish it. No recipient IDs, webhook URLs,
message contents or provider responses are retained in the receipt.
Test sends update health but never acknowledge real alerts or reset their cooldown.

For multiple groups, add separate entries with unique IDs and separate references.
To retain native notifications too, explicitly include
`{"id": "native", "provider": "native"}`. Omitting `destinations` preserves the
existing native-only default. An empty list disables delivery; reports still work.

## Automatic delivery and privacy

`monitor tick --notify` and `monitor scan --notify` deliver current evaluated alerts
to configured destinations. Plain scans, status, reports and configuration checks
never send. Installation remains an explicit step described in the CI recipe.

Remote text is a readable summary built only from these fields: non-identifying
operator label, reason, severity, measurement time, free GiB, comparable disk-use
change and interval, folder growth and interval where applicable, memory pressure,
coverage and a fixed safe next action. Capacities are rounded to tenths of a GiB;
unknown measurements display Unavailable/Unknown, and old scan coverage is marked stale.
Private alert titles, paths, filenames, repository and branch names, hostnames,
addresses, database names, arguments, environment values, credentials and raw
errors are not forwarded. Provider routing and signing necessarily use the private
configured endpoint/secret; they are not included in the alert's measurement fields
or public receipts. No local `file://` link is presented as remotely accessible.
Detailed inventory remains in the private local report; transfer it only through
an independently authorized channel when asking an agent to audit it.

iMessage uses the same payload allowlist. Its settings have exactly one field,
`recipient`, containing a phone number or email in Keychain or an existing secure
environment mechanism. Guided setup saves it privately. The adapter selects a
connected iMessage account using AppleScript and the Messages scripting dictionary, without reading
chat history or Contacts. The account's normal sender identity remains visible.
A signed-in GUI session and owner-approved Automation access are required; see
[setup](setup.md). No SMS/RCS fallback is implemented. Recipient/text travel over
stdin, not argv or files; native execution has a ten-second timeout with teardown.

The existing SQLite history also holds bounded delivery state for at most eight
destinations. Feishu acknowledges only a valid application success response
(`code: 0`, or compatible `StatusCode: 0`); HTTP 200 alone is not enough. iMessage acknowledges
only native scripting acceptance. Neither proves recipient receipt. A failed
destination cannot suppress another. Existing severity escalation,
native cooldowns on upgrade and cooldowns across restarts are preserved.

Each Feishu attempt uses a five-second socket timeout, a 64-KiB response cap and an
eight-second total request deadline. A separate HTTP worker receives its input
through a private pipe; on deadline it is killed and reaped, including when a
provider slowly drips response bytes. Credentials never enter its command arguments.
There is no immediate retry loop. Retryable failures start a
persisted 30-second exponential backoff, capped at one hour, respecting bounded
numeric `Retry-After` values. Nonretryable configuration/provider rejections wait
an hour before another automatic attempt. Attempts only occur on monitor
invocations while an alert is current; the probe interval can delay retries further.
Severity escalation bypasses a lower-severity failure backoff. Failed sends do not
acknowledge delivery. A timeout after provider acceptance can still yield a duplicate
on retry: custom-bot delivery has no transactional exactly-once guarantee.

Disable a destination by removing its entry and run a check. For complete rollback,
restore the installation manifest as described in the CI recipe. Removing a
destination does not delete reports, data or Keychain items.

## Verification boundaries

Automated verification uses synthetic paths/settings, a real loopback HTTP server,
real SQLite restarts and controlled native credential boundaries. Ordinary tests
do not send to real Feishu/Lark groups or query real Keychain items.

The outstanding deployment gates are an authorized dedicated test group, bot
webhook/signing settings available to the installed process, permitted unattended
Keychain/session access where selected, an explicitly authorized real test send,
and independent observation in that group. No real Feishu/Lark receipt is claimed
without those steps. iMessage additionally requires sign-in, approved Automation
access for the installed identity and independently observed receipt. Current native
tests use a controlled bridge/executable; real iMessage delivery is not verified.
Slack and WhatsApp still require implementation and provider-specific validation
before issue #6 can be closed.
