# Agent Storage Guard development

This package monitors workstation storage/memory and guards managed commands and their explicitly allocated scratch. Keep it headless and independent of any particular agent harness or backup system.

## Safety invariants

- Never permanently delete run data. Retire only validated owned roots by a same-volume rename; preserve uncertain, active, replaced or unowned paths.
- Use one registry/lock for admission and recovery. PID alone is not process identity. A marker in writable scratch is not cleanup authority.
- Preserve source, Git state, uncommitted work and retained evidence. Trash still consumes space.
- Never store command arguments, environment values or credentials in receipts. Use synthetic fixtures for examples and published evidence.
- Fail visibly on incomplete measurements, corrupt state and cleanup failures. Do not claim a watchdog is a hard filesystem quota.
- Preserve original baselines on scan gaps. Store history and alert delivery state in the same bounded SQLite database; only acknowledge notifications after the OS accepts them. Validate alert actions through the installed native helper, including a click after its sender exits.
- Report observations separately from causes. Swap size, uptime and a large folder alone cannot establish a leak or authorize disposal. Alerts include a concrete safe next action.

## Workflow

Use Python 3.11+ and uv. Work on a feature branch and use pull requests. Runtime dependencies should remain standard-library only unless a demonstrated requirement needs more.

Run `uv run pytest` and `uv run pytest --collect-only -q`. Process tests require unconditional teardown. Test concurrent admission, interruption, parent death, path replacement, stale identity, permission gaps and recovery with real small files/processes. Never fill the host disk to test low space.

Native notification tests use a separate bundle identity so temporary helpers cannot redirect the installed app's notification actions or inspect its permissions.

Keep modules under 500 lines. Add settings only with all consumers and an observable behavior test. Before publishing, inspect the tracked tree for private paths, local inventory, credentials and unnecessary generated artifacts.
