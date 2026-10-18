# Private setup and iMessage implementation evidence

This extends the Feishu-first commit `47e895027b8c3b98c163501ced5f8903df5b35a5`
on PR #7. Feishu/Lark, iMessage and native notifications are implemented; Slack and
WhatsApp remain unimplemented, so issue #6 stays open. This is draft implementation
evidence, not real-provider acceptance or production readiness.

## Observed validation

- `uv run pytest --collect-only -q`: 378 tests collected without errors.
- `uv run pytest -q`: 378 passed on macOS.
- Actual terminal/PTY setup tests enter synthetic webhook/signing values or a
  synthetic iMessage recipient without echo. The real CLI/config flow runs while
  only the native credential boundary is controlled. Transcript and every state
  file are checked to exclude those values. Non-TTY input fails before state creation.
- Native credential tests cover exact service/account/value arguments, add-only
  storage, duplicate-item rejection, locked/denied failures, bounded content and
  restoration of non-interactive state. No real Keychain item is queried or written.
- iMessage tests use a controlled native executable and an explicitly substituted
  JXA bridge. They cover connected iMessage selection, disabled/disconnected accounts,
  no SMS/RCS fallback, permission denial, raw-error redaction, literal injected text,
  ten-second process timeout and child reaping. The installed Messages scripting
  dictionary was read as an API specification; chat history/Contacts are not used.
- A real compiled native helper with a separate test bundle identity launches the
  real CLI and the controlled iMessage executable. It persists an acceptance receipt
  while leaving real-alert acknowledgements and host samples untouched. Test execution
  does not require actual Messages sign-in or Automation approval.
- Native helper tests cover literal argument forwarding, destination validation,
  state-binding rejection before child launch, code-signature verification, unchanged
  bundle reuse and preservation/rebuild when the manifest changes. State is verified
  by both the caller and helper to reject a stale helper rebound to another monitor.
- `uv build`: wheel and source distribution built. An isolated wheel installation
  exposes setup/provider options and refuses non-interactive credential entry without
  creating state. Required modules and distribution notices are present.
- Clean and repeated source installation with `uv tool install --reinstall .`
  passed using isolated tool/bin directories, leaving the user's installed tools
  untouched. Setup options were available from the resulting installed executable.
- `git diff --check`, documentation-link validation and publication-candidate
  private-workspace/host-reference checks passed. All published examples are synthetic.

Failing tests preceded implementation of Keychain writes, setup CLI commands,
installed-helper test routing and state binding. Existing monitor, guard, history,
report and native-notification regressions remain in the full suite.

## Limits and remaining owner steps

These limits describe this setup slice. The [later authorized iMessage test](issue-6-readable-alerts-imessage-fix.md#authorized-live-verification)
records installed acceptance and owner-observed receipt; the Feishu gate below
remains without a tracked live receipt.

The live Feishu test-group gate from the earlier receipt remains. For iMessage,
the owner must sign into Messages, choose an iMessage-capable recipient, authorize
the actual installed sender under Automation, explicitly test-send and independently
observe receipt. Keychain creation/access using the installed runtime and intended
session remains an environment gate. No account/session settings or production
permission grants were changed during this work.

Early JXA mock development runs timed out without output and were discarded as
verification evidence; those runs do not prove that the application bridge was
unreached. Final tests explicitly substitute the bridge. No real recipient was
configured and no actual message receipt was observed. Native command acceptance
is separate from recipient receipt; sender account identity is visible through iMessage.

See [the setup instructions](../setup.md) for the exact private commands and OS
steps. No secret/recipient values belong in command arguments, configuration,
reports, issue/PR artifacts or Git. Actual values are captured by hidden prompts
and stored only in Keychain. GitHub check runs must independently be terminal green
for the published head; these local results do not substitute for them.
