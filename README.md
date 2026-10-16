# Agent Storage Guard

Local disk growth history, actionable storage and memory alerts, and reversible scratch ownership for managed commands.

**Experimental.** One installation includes a read-only workstation monitor and a command guard. It works independently of TreeMap, Disk Steward or any agent runner. The guard protects commands launched through it; it is not a security sandbox or a hard filesystem quota.

## Why this component

Several independent coding-agent sessions can each start a package installation, test or media job against the same remaining disk space. A disk-usage report notices the result; the command creating the temporary data also needs a budget and a lifetime.

The guard serializes admission through one local registry, reserves an estimate, gives the child an owned temporary root, checks free space independently of scratch scans, and retires the root after checked completion. It never permanently deletes run data.

Existing projects already solve adjacent problems. See [the reuse decision](docs/reuse.md), [acknowledgements](ACKNOWLEDGEMENTS.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

## Requirements and installation

- Python 3.11+, macOS or Linux.
- Native `du`, `ps` and `lsof`. macOS provides these; Linux installations must provide `lsof` as well. Missing/incomplete process or file-open evidence fails closed.
- No runtime Python dependencies, network service or automatic tool downloads. Monitoring uses short scheduled processes and one private SQLite store.
- macOS native notifications with report/Activity Monitor actions use a tiny helper compiled with Apple's installed `swiftc` and frameworks. Core monitoring and the guard do not need Swift. Linux notifications optionally use an already installed `notify-send`.

From a checkout:

```sh
uv sync --locked
uv run agent-storage-guard --help
uv run agent-storage-guard run --estimate-gib 2 --label project-tests -- uv run pytest
```

For a locally installed command, run `uv tool install .` from the checkout. No package is published on PyPI by this repository setup.

## Recommended workstation setup

```sh
agent-storage-guard monitor init
agent-storage-guard monitor scan
agent-storage-guard monitor report --open
agent-storage-guard monitor install --activate
```

The macOS installation measures free space and memory every five minutes, and configured folders hourly. Notifications explain the evidence and next action: review recent growers, move finished work, or save work and quit unused apps through Activity Monitor. Unreadable folders produce one grouped notice, with static gaps suppressed. Nothing is automatically deleted, stopped or restarted.

The notification opens a local HTML overview in your browser: current status, useful changes over the available hours/day/week, storage areas and their deltas, specific next actions, then a collapsed evidence appendix. Review controls open the relevant folder details and filter them; copy controls provide actual folder paths, including the real parent for virtual prefix groups. Bookmark the page or use `monitor report --open` to find it again. No web service or remote assets are needed.

Use **Copy investigation prompt** under **What you can do** to hand the current evidence to your coding agent. The preview includes local paths, measurement limits, and cleanup approval boundaries. A manual-copy fallback works when browser clipboard access is unavailable.

Use `monitor init --root /absolute/path/to/dev` (repeatable) to choose roots instead of the defaults. Edit the private `~/.asg/monitor/config.json` for thresholds or intervals; rerun `monitor install --activate` after changing scheduling intervals. Baselines, bounded history and alert state live in the same local SQLite database. The overview is `~/.asg/monitor/report.html`; `monitor status` remains a terminal text view, and `monitor status --json` exposes evidence for integrations.

To migrate the earlier pc-backup monitor, run this **before** initializing the new monitor:

```sh
agent-storage-guard monitor migrate --from-state ~/.local/state/pc-backup/disk
agent-storage-guard monitor status
agent-storage-guard monitor install --activate
```

Migration preserves original baseline timestamps, complete/incomplete evidence and history without modifying source files. It refuses a busy legacy scan; retry once the scan completes. Activation replaces only the legacy disk monitor jobs and archives their plists with an explicit rollback manifest; backup schedules are unaffected. Run `monitor rollback /absolute/path/to/manifest.json` to restore the previous jobs. [Monitoring behavior, budgets and platform limits](docs/monitoring.md) describes retention and safe responses.

## Run, inspect and recover

```sh
agent-storage-guard run --estimate-gib 2 --label video-tests -- npm test
agent-storage-guard status
agent-storage-guard reconcile
```

Default policy is two simultaneous guarded runs, a 20 GiB projected free-space reserve, and one-second scratch polling. Override with `--max-runs`, `--floor-gib` and `--poll-seconds` on `run`. Use a consistent policy and the same state directory across independent sessions; a separate registry cannot coordinate the same reserve. Each active estimate is reserved conservatively, so already allocated scratch can be counted again rather than admit an unsafe run.

`TMPDIR`, `TMP` and `TEMP` point to the run's scratch directory. Children inherit them. A nested guard shares its validated outer run instead of charging another reservation; it cannot enlarge that run's scratch budget or weaken its actual controller. Ordinary program output remains attached to the terminal.

The state directory defaults to `~/.asg` (or inherited `ASG_STATE_DIR` for nested runs). The short default leaves room for Unix sockets used by test runners; unusually long home or explicit state paths may need a shorter same-volume location. Global `--state` and `--retirement` options come before the subcommand. macOS retirement defaults to `~/.Trash`; Linux uses a private `retired` directory beneath state. Both locations must be on the scratch filesystem. External-volume state needs a same-volume retirement destination; no large implicit copy occurs.

A successful retirement is a rename to `asg-<run UUID>`. Receipts under `state/receipts` record the original path, retired path, outcome, process/filesystem identity and a bounded user label. Command arguments and environment values are not persisted. Labels are user-provided metadata: do not put secrets in them.

**Retirement does not reclaim capacity.** Trash and retained bundles remain charged to volume usage. Permanent disposal is a separate user decision.

To recover retired data, read its receipt and move the indicated bundle to a separate safe folder on the same volume; inspect it there. Do not overwrite an existing path, edit the ownership registry, or put the bundle back under active scratch. Unknown, active or replaced directories are preserved, not adopted for cleanup.

## Safety boundary

- Cleanup authority comes from the private registry plus root identity, not a writable marker, name pattern or age.
- Symlinked/unowned state, inode replacement, corrupt records, failed measurements, cross-volume retirement and unavailable liveness evidence refuse unsafe operations.
- Normal completion, command failure and interruption stop the managed group before retirement. Killing the outer wrapper closes a controller pipe and denies/stops its managed child.
- If the controller itself is killed and its process identity is lost, the guard preserves scratch and its reservation rather than signal a potentially unrelated group. Remaining writers may require operator intervention. Deliberately detached processes are outside the process-group guarantee.
- Tests that deliberately remove read access inside scratch can trigger measurement failure and stop the run. Keep this compatibility constraint in mind for permission/error-injection suites; the guard never treats unreadable bytes as zero.
- Writes outside owned temporary storage remain outside the scratch ceiling. Whole-volume pressure is checked separately. A fast write can consume space before a sample; a slow measurement has a bounded termination allowance. These are watchdog checks, not kernel quotas.
- The wrapper is for cooperative development tools. A hostile process running as the same user can tamper with that user's state; use an existing sandbox or quota backend when an enforceable write boundary is required.

A cleanup failure is not success: preserve the run and reservation, write the failure receipt when possible, and return a failing status. Corrupt/unwritable state can prevent a receipt; the command must still fail visibly. Receipt history is retained, not automatically pruned.

## Exit codes

Child exit status is preserved for ordinary completed runs. Guard refusal/budget pressure uses 75; measurement or cleanup failure uses 74; interruption follows the usual 128 + signal convention. A successful child can therefore result in a failing guard exit when cleanup or accounting did not complete.

## Development and integration

```sh
uv run pytest --collect-only -q
uv run pytest -q
uv build
```

Tests use small real files and subprocesses, exercise concurrent starts, interruption, owner death, root replacement, corrupt state, sparse/hardlink accounting and permission failures. CI runs macOS and Linux on Python 3.11 and 3.13. No test should fill the host disk to simulate low space.

See [integration examples](docs/integrations.md), [measurement details](docs/measurement.md), [monitoring](docs/monitoring.md) and [developer instructions](AGENTS.md). Installation does not rewrite other agents' commands or install global agent hooks. Wrap concrete test/build commands with `run` to enforce their scratch budgets.
