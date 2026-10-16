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

Native notifications identify Agent Storage Guard and provide an **Open storage overview** action opening `report.html` in your browser. Memory alerts also offer **Open Activity Monitor**. macOS controls permission, banner presentation and Focus suppression; accepted enqueue is not proof of visible delivery. Failed permission/enqueue attempts are not marked delivered. The AppleScript fallback has reduced identity/action support.

## Read the overview

The default view has five parts, in this order:

1. **Current status:** available disk space, shortfall from the reserve, current memory pressure, swap and actual measurement time. Old or missing evidence is labeled.
2. **What changed:** the useful comparison available from actual hours/day/week history. A day of folder history remains visible even when frequent capacity probes started only minutes ago; their intervals are shown separately.
3. **Where storage is used:** projects/worktrees, agent data, temporary work, package caches and other configured areas. Nested roots are counted once. Partial areas distinguish current measured subsets, earlier measurements and unknown remainders; folder accounting is not physical reclaim.
4. **What you can do:** prioritized recommendations tied to measured locations, their sizes and changes. Review buttons open the related evidence. Older large outputs require a fresh check before any cleanup, and active/unfinished work remains protected.
5. **Details and evidence:** folded by default. Searchable paths, per-folder measurements, coverage gaps, baseline details and Git registrations are disclosed here. Virtual groups copy their actual parent location; use Finder's Go to Folder to inspect it.

`agent-storage-guard monitor report --open` refreshes the inexpensive capacity/memory probe and opens the HTML overview. It does not start a slow scan. `monitor scan` requests a folder pass. The saved HTML works offline without a server or external assets. The reload button reloads the latest saved file; it does not initiate a measurement or delete files. The text output remains available for terminal use.

A menu bar entry can later expose this same overview and monitor health; it is not installed by this version.

Under **What you can do**, **Copy investigation prompt** creates a concise brief for a coding agent: current disk/memory status, actual comparison periods, major storage areas, leading growers and older suspects, scan gaps, and the exact local report path for full evidence. Preview the text before sharing; it includes local paths. If clipboard access fails, the preview opens and selects the prompt for manual copying. The brief directs the agent to refresh uncertain measurements, protect unfinished work, investigate causes and prevention, and obtain exact cleanup approval before reversible staging. Copying a prompt does not authorize or execute cleanup, upload data, or contact an agent.

Warnings normally repeat at most every six hours, urgent alerts hourly. Severity escalation bypasses cooldown. Reserve recovery requires two samples above a 2 GiB recovery margin. Warning confirmation requires samples between half and 2.5 times the configured probe interval; repeated immediate manual invocations do not establish sustained pressure. Growth alerts deduplicate overlapping parent/child paths and require further material growth before repeating. Stale or unsupported evidence is not a healthy measurement.

## Evidence and budgets

The private `state/monitor/monitor.sqlite3` is the active authority for original folder baselines, folder history, capacity/memory history and alert decisions. The HTML overview and terminal text report are derived from it. The overview loads a bounded set of recent and hour/day/week reference snapshots, instead of reloading the entire retained history on every probe. The earlier prototype's JSON files remain unchanged for rollback; their monitor jobs are disabled after activation. There is no service, telemetry endpoint or network upload.

- Folder measurements run sequentially through native `du`. Output is aggregated as it streams, so a large number of temporary folders cannot exhaust a raw-output buffer. Record lengths and retained folder details stay bounded; detail truncation retains root totals and disables absence inference for omitted children. Every root gets a first turn before spare time is used to retry slow roots. Completed ungrouped subtrees survive deadlines; interrupted prefix groups stay incomplete because not all matching siblings have been visited. Per-root and whole-pass deadlines still bound resource use, and exhausted passes rotate the first root next time. The inexpensive capacity/memory probe has its own scheduled job.
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

## Protected folders on macOS

On macOS, installation builds **Agent Storage Guard.app**, with original local artwork and a project bundle identifier, in `~/Applications`. The two launchd jobs run through this native app; it remains the parent of the configured Python monitor until that worker exits. The app accepts only `--monitor tick` or `--monitor scan`, with CLI and state destinations fixed in its signed manifest. Shutdown interrupts the worker so scanner teardown can reap native accounting processes. The shared Python runtime remains a dependency; it is not the permission target.

Trash and protected application temp folders may need **Full Disk Access for Agent Storage Guard.app**. Add the exact app path shown in the report under System Settings → Privacy & Security → Full Disk Access. Do not add a shared Python interpreter. Full Disk Access is broad OS access, so make this explicit user decision only after reviewing the installed app. The initial Touch ID prompt may identify System Settings rather than the target app; the selected Full Disk Access entry must show Agent Storage Guard and its drive/check icon.

The bundle is compiled from this package's Swift source and ad-hoc signed locally; it is not Apple-notarized. Updating the signed bundle may require reapproval. Granting permission is not proof of coverage: rerun the **installed scheduled scan** and verify complete root totals. Permission failures retain the native reason beside the affected storage area. The monitor does not bypass macOS protections or synthesize a Trash total from a stale Finder listing.
