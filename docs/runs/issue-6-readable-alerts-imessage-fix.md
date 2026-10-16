# Readable remote alerts and iMessage scripting correction

## Observable changes

Remote messages use a heading, separate measurement lines, UTC time and a fixed
next action, all constructed from the existing strict allowlist. Synthetic alerts
say TEST ALERT and identify their measurements as samples. Unknown values remain
explicit. Installed test failures preserve only known safe error codes and give
an actionable gate; arbitrary helper diagnostics remain suppressed.

The native adapter now uses AppleScript rather than JXA for Messages enumeration
and sending. Recipient and text remain escaped data over stdin, never argv/files.
The ten-second bound and child teardown remain unchanged. No SMS/RCS fallback.

## Investigation and evidence

A read-only, no-send probe reproduced a JXA coercion failure (-1700) for the
service-type account filter. The equivalent AppleScript filter and connection
check completed successfully. This proves a scripting incompatibility; it does
not establish the cause of an earlier installed timeout or prove message receipt.
No conversation history, contacts or recipient values were read by these probes.

Five regression assertions failed before the presentation/error-receipt fixes.
The changed delivery files then passed 47 focused tests, including real local HTTP,
SQLite, process teardown and the compiled helper -> real CLI -> controlled native
executable path. The actual AppleScript sender compiles against the installed
Messages dictionary; a real AppleScript literal evaluation verifies quotes,
backslashes, newlines and Unicode stay data. Tests do not send to real recipients.

The old JXA-only bridge substitutes were replaced by compiler/literal checks for
the actual AppleScript path; provider outcome and private-stdin tests remain.

Final local validation: `uv run pytest -q` passed 377 tests;
`uv run pytest --collect-only -q` collected 377 without errors.
`git diff --check` passed.

## Remaining owner verification

A successful no-send account check does not prove the installed responsible app's
Automation or Keychain access, nor native send acceptance or destination receipt.
An explicitly authorized installed test and independently observed receipt are
still required. A timeout leaves receipt uncertain: check before retrying. No
permissions, account settings or activation were changed during this work.
