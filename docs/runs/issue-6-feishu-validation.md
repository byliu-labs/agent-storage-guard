# Issue 6: Feishu-first implementation evidence

Historical receipt for the initial Feishu commit `47e8950`. The subsequent
[setup and iMessage receipt](issue-6-setup-imessage-validation.md) describes the
expanded implementation; the scope and counts below belong to the earlier snapshot.

Scope: Feishu/Lark group bots and native delivery compatibility, following the
request to build Feishu first. Base: `main` at
`6ce51c14a0d9d4605bf4a247161e2ae6f571c4cf`. This is not completion of issue #6;
Slack, WhatsApp and iMessage remain unimplemented.

All published fixtures and examples are synthetic. No real provider destination,
credentials, machine inventory, account details or private report is attached.

## Verification performed

- `uv run pytest --collect-only -q`: 313 tests collected without errors.
- `uv run pytest -q`: 313 passed.
- `uv run pytest tests/test_delivery_transports.py tests/test_delivery_e2e.py -q`:
  real loopback HTTP, worker subprocesses and SQLite persistence. Subsequent
  regressions are included in the final full-suite count above.
- `uv run pytest tests/test_ci_monitor_e2e.py`: real small-file writer during CLI
  scans, stable-area evidence, overlapping-root accounting, baseline preservation
  through a missing root, controlled launchctl activation/rollback and unrelated
  service preservation. No real launchd jobs were activated.
- `uv build`: wheel and source distribution built. Wheel includes the delivery
  modules and required notices. An isolated installation from that wheel passed
  `monitor init` and a configured offline `monitor delivery-check`; acceptance
  remained `not_tested`, and no provider request was made.
- `git diff --check`: passed. Candidate publication files were checked for private
  workspace/host references. Detailed inventory and generated distributions are
  excluded from the committed evidence.

New operator/delivery behaviors are covered by `test_delivery_cli.py`,
`test_delivery_policy.py`, `test_delivery_transports.py`,
`test_delivery_credentials.py` and `test_delivery_e2e.py`.
Tests exercise success, application rejection despite HTTP 200, malformed and
oversized responses, network/protocol errors, socket timeout, absolute slow-drip
deadline with child reaping, signing, bounded rate-limit delays, malformed settings,
redaction, independent destinations, restarts, cooldowns, escalation, stale evidence,
native migration and delivery failure without suppressing folder measurements.

## Test-first findings and fixes

Missing CLI commands and payload/dispatcher interfaces failed before implementation.
Further failing regressions proved these fixes:

- A remote synthetic test now identifies itself in the received message.
- Socket inactivity timeout is supplemented by an eight-second worker deadline.
- Expired scan evidence keeps delivery acknowledgements until actual recovery.
- A deliberate test-send during upgrade preserves native cooldown history.
- A provider/credential failure cannot prevent the scheduled folder scan.

One existing half-second scanner fixture failed during an earlier full run, then
passed alone; subsequent full runs passed. No scanner implementation or deadline
fixture was changed for this delivery slice.

## Outstanding deployment gates

No real Feishu/Lark test message was sent, and no actual Keychain item was queried.
The remaining requirements are an explicitly authorized dedicated test group,
its webhook and signing configuration through an approved credential reference,
access by the actual installed scheduled identity/session, an authorized test-send,
and independent observation in the destination group. A locked or inaccessible
Keychain is an explicit gate; tests use a controlled native boundary without
granting permissions or changing account/session settings.

Provider acceptance and independently observed receipt remain separate. Local
HTTP success is protocol evidence and does not prove Feishu service acceptance.
The real native notification click flow is unchanged by this slice; no new
production notification permission or action validation is claimed here.

GitHub checks must be terminal green for the published commit. Their results belong
to the PR's check runs; this local receipt does not substitute for them.
