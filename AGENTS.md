# Agent Storage Guard development

This package guards managed commands and their explicitly allocated scratch. Keep it small, headless and independent of any particular agent harness or backup system.

## Safety invariants

- Never permanently delete run data. Retire only validated owned roots by a same-volume rename; preserve uncertain, active, replaced or unowned paths.
- Use one registry/lock for admission and recovery. PID alone is not process identity. A marker in writable scratch is not cleanup authority.
- Preserve source, Git state, uncommitted work and retained evidence. Trash still consumes space.
- Never store command arguments, environment values or credentials in receipts. Use synthetic fixtures for examples and published evidence.
- Fail visibly on incomplete measurements, corrupt state and cleanup failures. Do not claim a watchdog is a hard filesystem quota.

## Workflow

Use Python 3.11+ and uv. Work on a feature branch and use pull requests. Runtime dependencies should remain standard-library only unless a demonstrated requirement needs more.

Run `uv run pytest` and `uv run pytest --collect-only -q`. Process tests require unconditional teardown. Test concurrent admission, interruption, parent death, path replacement, stale identity, permission gaps and recovery with real small files/processes. Never fill the host disk to test low space.

Keep modules under 500 lines. Add settings only with all consumers and an observable behavior test. Before publishing, inspect the tracked tree for private paths, local inventory, credentials and unnecessary generated artifacts.
