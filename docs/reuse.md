# Reuse decision

Checked 2026-10-14. This is a scoped source review, not proof that no equivalent tool exists anywhere.

The requirement is shared projected storage admission, a physical free-space reserve, a scratch-accounting ceiling and reversible ownership-based retirement around ordinary test commands. Existing scanners, worktree managers and agent runners remain useful and are not replaced.

| Existing project | Verified scope | Integration decision |
|---|---|---|
| Delegate Agent | Owned scratch, child supervision, terminal-run retention and a captured-output limit. Its scratch budget is a cleanup **time** deadline. | Use its lifecycle ideas and existing wrapper seams; do not copy its irreversible deletion policy. A Delegate-only change would not cover independent direct test commands. |
| Disk Steward | Live capacity/growth evidence, session correlation, budgets for its own CPU/RSS/database/events. | Keep monitoring independent; no second evidence database in this guard. |
| Worktrunk | Worktree management, COW cache reuse, lifecycle hooks and command aliases. | Wrap a concrete test/build command; do not create a competing worktree manager or plugin framework. |
| TreeMap | Disk scans/history, scanner resource budgets and limits on its own cleanup operations. | Use for visual investigation; contribute coverage-history corrections upstream separately. |

Pinned primary sources:

- [Delegate configuration](https://github.com/treygoff24/delegate-agent/blob/b4ac9413e484c57c522a9d0c4671104867daa0d9/docs/configuration.md) and [scratch deadline](https://github.com/treygoff24/delegate-agent/blob/b4ac9413e484c57c522a9d0c4671104867daa0d9/src/delegate_agent/run_scratch.py).
- [Disk Steward resource budgets](https://github.com/KMerdan/disk_steward/blob/202c4e161ac62dde13dec790acdab0963570be67/Sources/DiskStewardCore/ResourceControl/ResourceBudget.swift) and [notifications](https://github.com/KMerdan/disk_steward/blob/202c4e161ac62dde13dec790acdab0963570be67/Sources/DiskStewardApp/Notifications/ThresholdNotificationPolicy.swift).
- [Worktrunk hooks](https://github.com/max-sixty/worktrunk/blob/dba1c64ae9affc7fb4600f688341d8ac773bfdb4/docs/public/hook.md).
- [TreeMap source](https://github.com/Prithvi-Web/TreeMap-Disk-Visualizer/tree/2c28ff5bc2083ea178b4b9ff0ca18dc4ccd02b43).

None of the inspected interfaces provided the combined guard for independent commands. This repository extracts the existing small prototype for that gap. It deliberately invokes native `du` instead of adding another scanner. It does not implement a monitoring daemon, agent orchestrator, worktree manager, filesystem sandbox or MCP server.

Review this choice when an upstream adapter supplies the same behavior with less maintenance. Do not market this as the first agent-aware storage tool.
