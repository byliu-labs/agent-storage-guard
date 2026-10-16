# Reuse decision

Checked 2026-10-15. This is a scoped source review, not proof that no equivalent tool exists anywhere.

The requirement combines shared projected storage admission and reversible owned scratch with independently useful local baselines/history and actionable disk/memory alerts. Existing scanners, worktree managers and agent runners remain useful. The built-in monitor removes the need to install another complete application for basic investigation.

| Existing project | Verified scope | Integration decision |
|---|---|---|
| Delegate Agent | Owned scratch, child supervision, terminal-run retention and a captured-output limit. Its scratch budget is a cleanup **time** deadline. | Use its lifecycle ideas and existing wrapper seams; do not copy its irreversible deletion policy. A Delegate-only change would not cover independent direct test commands. |
| Disk Steward | Live capacity/growth evidence, session correlation, budgets for its own CPU/RSS/database/events. | Learn from its alerts, freshness/coverage semantics and budgets; use native measurements and one local SQLite store without porting its whole Swift app. |
| Worktrunk | Worktree management, COW cache reuse, lifecycle hooks and command aliases. | Wrap a concrete test/build command; do not create a competing worktree manager or plugin framework. |
| TreeMap | Disk scans/history, scanner resource budgets and limits on its own cleanup operations. | Optional visual investigation. The built-in monitor supplies basic history; coverage-history corrections remain a separate upstream contribution. |
| gdu | Fast native filesystem analysis and JSON export. Its inspected export does not preserve read-error coverage. | Optional read-only investigation; do not treat exported totals as a complete baseline without a reliable coverage channel. |

Pinned primary sources:

- [Delegate configuration](https://github.com/treygoff24/delegate-agent/blob/b4ac9413e484c57c522a9d0c4671104867daa0d9/docs/configuration.md) and [scratch deadline](https://github.com/treygoff24/delegate-agent/blob/b4ac9413e484c57c522a9d0c4671104867daa0d9/src/delegate_agent/run_scratch.py).
- [Disk Steward resource budgets](https://github.com/KMerdan/disk_steward/blob/202c4e161ac62dde13dec790acdab0963570be67/Sources/DiskStewardCore/ResourceControl/ResourceBudget.swift) and [notifications](https://github.com/KMerdan/disk_steward/blob/202c4e161ac62dde13dec790acdab0963570be67/Sources/DiskStewardApp/Notifications/ThresholdNotificationPolicy.swift).
- [Worktrunk hooks](https://github.com/max-sixty/worktrunk/blob/dba1c64ae9affc7fb4600f688341d8ac773bfdb4/docs/public/hook.md).
- [TreeMap source](https://github.com/Prithvi-Web/TreeMap-Disk-Visualizer/tree/2c28ff5bc2083ea178b4b9ff0ca18dc4ccd02b43).

None of the inspected interfaces supplied the complete agreed workflow without coupling users to another full application. This repository hardens its existing guard and monitor prototypes. It reuses native `du`, Git worktree inventory and Python/SQLite, and builds only a small native notification bridge against installed Apple frameworks. It does not vendor other applications' scaffolding or source. This avoids an additional Go, Rust, Node or Swift application runtime for basic monitoring.

The inspected [gdu exporter](https://github.com/dundee/gdu/blob/387cf50377e53bad628603658cfb76fd4a54d718/pkg/analyze/encode.go) lacks a complete read-error coverage record. TreeMap's snapshot service is coupled to its scan store and application types; Worktrunk's lifecycle is part of its Rust worktree manager. Reusing native interfaces here is smaller than extracting their internals. Reconsider an adapter when it preserves complete/partial/unknown evidence with less maintenance.

Review this choice when an upstream adapter supplies the same behavior with less maintenance. Do not market this as the first agent-aware storage tool.
