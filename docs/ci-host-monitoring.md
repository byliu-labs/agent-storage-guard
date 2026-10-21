# Monitor an unattended CI Mac

This is an opt-in recipe. All paths below are synthetic: replace them with existing, narrowly scoped directories on the CI host. Monitoring does not wrap CI commands, stop jobs, remove data or change PostgreSQL settings. The command guard remains a separate opt-in tool.

## Initialize and establish a baseline

Choose runner workspaces, transferred test snapshots, dependency caches, retired scratch, Trash and local test-database storage. Do not choose an entire home directory or credential store. Include stable large storage areas as well as temporary growers.

```sh
agent-storage-guard --state /Volumes/CIExample/guard-state monitor init \
  --root /Volumes/CIExample/workspaces \
  --root /Volumes/CIExample/transferred-snapshots \
  --root /Volumes/CIExample/dependency-caches \
  --root /Volumes/CIExample/retired-scratch \
  --root /Volumes/CIExample/.Trash \
  --root /Volumes/CIExample/test-database-storage
```

Before activation, edit `/Volumes/CIExample/guard-state/monitor/config.json`. For example, use `tick_interval_seconds: 60`, `scan_interval_seconds: 900`, `scan_timeout_seconds: 30` and `scan_total_timeout_seconds: 120`. Keep the JSON syntax valid. Roots support `depth` from 0 to 4; depth 0 is useful for database storage totals without per-database detail. Set reserve and growth thresholds appropriate to the volume and workload.

The lightweight capacity/memory probe and folder scanner have separate schedules. Per-root and total deadlines bound native accounting; completed subtrees remain usable when a deadline or access gap prevents full coverage. A deadline can leave unknown storage, so review coverage rather than assuming a short scan is complete. Worktree discovery has its own bounded budget after folder accounting. Neither scanner is a filesystem quota.

```sh
agent-storage-guard --state /Volumes/CIExample/guard-state monitor scan
agent-storage-guard --state /Volumes/CIExample/guard-state monitor report
```

Inspect the generated local HTML and confirm root totals, observation times and coverage before activation. Run another scan while a representative CI job is active; check elapsed time and gaps against the selected budgets. Never fill the disk to test pressure. Overlapping roots are alternative drill-down levels, not additive storage totals. A missing or inaccessible root remains explicit and does not replace its original baseline with zero.

Database directory accounting measures disk pressure only. A large directory, old file or idle connection does not establish that any database can be dropped. Monitoring does not query private database contents and confers no cleanup authority. Trash and retired scratch still consume capacity.

## Configure delivery, then activate explicitly

Start with [private setup](setup.md) for hidden Keychain capture, health checks and installed-identity test sends for Feishu/Lark and iMessage. Native notifications remain supported. Slack and WhatsApp are not yet implemented. Checking configuration must not send a message. Review authentication, recipient, session, OS permission and possible cost prerequisites before a real test. Provider acceptance and independently observed receipt are different evidence; iMessage requires a working graphical user session.

Use the installed CLI rather than an ephemeral development executable. Stage the two monitor jobs first:

```sh
agent-storage-guard --state /Volumes/CIExample/guard-state monitor install
```

Review its returned manifest and staged plists. On macOS, installation uses the named native app; granting Full Disk Access or notification/Automation permissions is an owner-operated step. A GUI launchd domain and signed-in session may be required; a headless login alone does not prove these prerequisites. Do not change runner services or CI definitions for this recipe.

```sh
agent-storage-guard --state /Volumes/CIExample/guard-state monitor install --activate
```

Retain the manifest path returned by the activated installation. Activation installs only the monitor's jobs and handles its recognized legacy monitor jobs; unrelated launchd jobs remain outside its scope. Changing schedule values requires another explicit installation. To restore the preceding monitor state, pass the exact returned manifest, for example:

```sh
agent-storage-guard --state /Volumes/CIExample/guard-state monitor rollback /Volumes/CIExample/guard-state/monitor/installations/EXAMPLE_INSTALL_ID/manifest.json
```

Rollback restores monitor job state and retains local reports/history and installation archives. It does not erase measured data or change provider accounts and permissions.

## Hand local evidence to an agent

The overview includes stable large branches, recent changes, stale/partial measurements and concrete safe next actions. Its collapsed `agent-context` JSON contains all retained folder evidence and configured roots, including those beyond the starting-point shortlist. A complete scan of configured roots is still not a complete machine audit.

Use **Copy investigation prompt** and review its private paths before sharing. Keep detailed inventory local; remote alerts carry only the explicitly allowed coarse fields. The investigation requires wider bounded discovery, coverage reconciliation and fresh safety checks before any proposal. See [monitoring](monitoring.md) and [cleanup handoff](cleanup-handoff.md). No alert, report or copied prompt authorizes cleanup, database dropping, process termination or uploads of inventory.

## Repeatable synthetic validation

`uv run pytest tests/test_ci_monitor_e2e.py` initializes custom roots through the CLI, scans during a real small-file writer process, checks stable storage and complete local evidence, verifies overlap accounting and baseline preservation after a root disappears, and exercises installation/rollback through a controlled launchctl process while preserving an unrelated job. The writer is always stopped and reaped. It does not activate real launchd jobs, send provider messages or inspect real host inventory; native activation and provider receipt require separate explicit environment validation.
