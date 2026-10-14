# Agent Storage Guard

Shared storage admission and reversible scratch ownership for managed test and development commands.

**Experimental.** This is a small extraction of a tested local guard. It protects commands launched through it; it is not a security sandbox or a hard filesystem quota. It does not replace your scanner, monitoring app, agent runner or worktree manager.

## Why this component

Several independent coding-agent sessions can each start a package installation, test or media job against the same remaining disk space. A disk-usage report notices the result; the command creating the temporary data also needs a budget and a lifetime.

The guard serializes admission through one local registry, reserves an estimate, gives the child an owned temporary root, checks free space independently of scratch scans, and retires the root after checked completion. It never permanently deletes run data.

Existing projects already solve adjacent problems. See [the reuse decision](docs/reuse.md), [acknowledgements](ACKNOWLEDGEMENTS.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

## Requirements and installation

- Python 3.11+, macOS or Linux.
- Native `du`, `ps` and `lsof`. macOS provides these; Linux installations must provide `lsof` as well. Missing/incomplete process or file-open evidence fails closed.
- No runtime Python dependencies, daemon, network service or automatic tool downloads.

From a checkout:

```sh
uv sync --locked
uv run agent-storage-guard --help
uv run agent-storage-guard run --estimate-gib 2 --label project-tests -- uv run pytest
```

For a locally installed command, run `uv tool install .` from the checkout. No package is published on PyPI by this repository setup.

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

See [integration examples](docs/integrations.md), [measurement details](docs/measurement.md) and [developer instructions](AGENTS.md). This version installs no agent hooks or monitoring schedule. Existing monitoring can remain the operational backstop.
