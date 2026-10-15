# Workstation monitoring

The monitor observes configured folders, the free space on its state volume, and macOS memory pressure. It never disposes of user files, quits applications, clears swap, or restarts the computer. Use the command guard separately for test/build admission and owned scratch.

## What an alert tells you

| Condition | Evidence | Suggested action |
|---|---|---|
| Reserve breached | Free bytes and shortfall below the configured reserve, confirmed by two appropriately spaced probes | Open the report, review recent growers, move finished work to another disk. Preserve active worktrees and unfinished outputs. |
| Urgent free space | Free bytes below the urgent threshold; no confirmation delay | Pause large builds and review finished work for offload. |
| Folder growth | Complete native accounting measurements, actual comparison interval, and a material delta | Review the named folder in context; move reviewed disposable outputs to Trash. Trash still consumes space. |
| Sustained memory pressure | macOS warning pressure on two spaced samples with the same boot identity; critical pressure alerts immediately | Save work, open Activity Monitor's Memory tab, and quit unused heavy apps normally. If pressure persists after that, consider restarting after saving. |
| Incomplete scan | One grouped summary of inaccessible locations, with detailed paths in the report | Choose narrower readable roots or review macOS Privacy access. Previous complete baselines stay intact. Static or improving gaps are not repeatedly announced. |

Large swap usage and long uptime alone do not establish a leak or justify a restart. Swap is managed by macOS. The monitor records swap, compressed bytes, RAM, uptime and pressure, and reports swap changes across retained samples from the same boot. It does not claim a process caused growth merely because the process was running nearby in time.

Native notifications identify Agent Storage Guard and provide an **Open storage report** action. Memory alerts also offer **Open Activity Monitor**. macOS controls permission, banner presentation and Focus suppression; accepted enqueue is not proof of visible delivery. Failed permission/enqueue attempts are not marked delivered. The AppleScript fallback has reduced identity/action support.

Warnings normally repeat at most every six hours, urgent alerts hourly. Severity escalation bypasses cooldown. Reserve recovery requires two samples above a 2 GiB recovery margin. Warning confirmation requires samples between half and 2.5 times the configured probe interval; repeated immediate manual invocations do not establish sustained pressure. Growth alerts deduplicate overlapping parent/child paths and require further material growth before repeating. Stale or unsupported evidence is not a healthy measurement.

## Evidence and budgets

The private `state/monitor/monitor.sqlite3` is the active authority for original folder baselines, folder history, capacity/memory history and alert decisions. The human report is derived from it. The earlier prototype's JSON files remain unchanged for rollback; their monitor jobs are disabled after activation. There is no service, telemetry endpoint or network upload.

- Folder measurements run sequentially through native `du`, with per-root and whole-pass deadlines, output limits and an aggregate-count limit. When a whole-pass deadline is reached, the next pass starts one root later so later roots cannot be permanently starved. The inexpensive capacity/memory probe has its own scheduled job, so it does not wait for a folder scan.
- Detailed folder samples retain up to 90 days/2160 samples; daily summaries retain two years/730 samples. Combined folder-history payloads are capped at 96 MiB. Original baselines are retained and bounded to 12000 aggregates.
- Capacity/memory samples retain up to seven days/2016 samples and 8 MiB of payloads. Comparisons across reboot identities are not reported as memory growth.
- Each payload is bounded to 4 MiB; the SQLite main database is capped at 256 MiB. SQLite transaction journals need temporary space in addition to this cap. Budget exhaustion fails visibly. This is a storage budget, not a kernel quota on every temporary byte.
- Scheduled stdout is discarded. Each private error log preserves up to a 1 MiB previous tail before shrinking its active inode on the next invocation. A failing invocation may temporarily exceed that threshold. Installation rollback archives are retained intentionally.
- Git discovery has depth, entry, repository and total-runtime limits. It invokes native `git worktree list --porcelain -z`; truncation and access gaps are reported. Inventory does not imply any worktree is disposable.

Accounting values include allocated blocks and native hardlink semantics. APFS cloned extents can be shared, and nested folder totals overlap. Never sum these numbers as guaranteed reclaim. Scan boundaries can differ from cheap volume probes; volume changes are not automatically attributed to a folder.

## Configuration and scheduling

`monitor init --root /absolute/path` selects explicit roots. Defaults cover existing development/agent/cache directories and macOS temporary directories, rather than scanning the entire home or protected application data. Nest paths, ancestors and descendants are refused. Symlink traversal and ambiguous native output are coverage gaps. macOS's system `/var` alias is handled while preserving configured history keys.

`state/monitor/config.json` holds reserve/growth thresholds in bytes, scan timeouts, integer scan/probe intervals, and roots with depth 0–4. A root can set `prefix_groups` to a list of literal name prefixes with depth 1; this aggregates many temporary folders without per-file history. Legacy boolean grouping is supported only for the older prototype's migration.

Threshold changes take effect on the next invocation. Scheduling changes require rerunning `monitor install --activate`; installed launchd intervals consume the same configuration. macOS scheduling is implemented; Linux users can run `monitor tick` and `monitor scan` under their existing scheduler. Native memory pressure collection currently supports macOS; Linux memory evidence is explicitly unavailable.

Activation preflights plists, archives previous monitor plists and loaded states, then replaces only the monitor jobs. A failed activation attempts rollback immediately and preserves its manifest. `monitor rollback MANIFEST` restores previous service state. Existing backup schedules are outside this operation.

## Sources and reuse

The monitor hardens this project's earlier local prototype. It reuses Python's standard SQLite interface and native OS/Git measurements, rather than bundling a second application. Disk Steward informs actionable notifications, freshness, coverage honesty and resource budgets; TreeMap informs baseline/history semantics. See [reuse decisions](reuse.md), [acknowledgements](../ACKNOWLEDGEMENTS.md) and [notices](../THIRD_PARTY_NOTICES.md).

Apple describes memory pressure as a combination of free memory, swap rate, wired memory and cached files, and swap as startup disk space used for memory. [Activity Monitor documentation](https://support.apple.com/en-euro/guide/activity-monitor/actmntr1004/mac).

The macOS sysctl exports dispatch pressure flags (normal=1, warning=2, critical=4); these differ from XNU's internal pressure enumeration. The collector follows the actual conversion in [Apple's XNU handler](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_memorystatus_notify.c) and [event constants](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/event_private.h), preserving unknown values instead of guessing.
