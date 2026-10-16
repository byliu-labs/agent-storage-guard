# Agent Storage Guard

Keep coding agents from quietly filling your disk. Get local disk and memory alerts, see which folders are growing, and give test/build commands a shared temporary-storage budget.

**Experimental · Local-only · No runtime Python dependencies**

| Component | What it does |
| --- | --- |
| Monitor | Records disk/memory pressure and folder growth; opens a local HTML report. |
| Command guard | Coordinates concurrent commands and watches their temporary storage. |
| [Sweep Computer Disk skill](skills/sweep-computer-disk/SKILL.md) | Guides your agent through a broader Mac audit and an itemized cleanup proposal. Cleanup requires your approval. |

Use them together: **monitor → investigate with the skill → approve specific cleanup**. Wrap future builds/tests with the guard to manage scratch growth. Each component also works independently.

## Install

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), Git, `du`, `ps` and `lsof`. The guard supports macOS and Linux; the sweep skill targets macOS. Native Mac notification actions also require Apple's installed `swiftc`; Linux notifications optionally use `notify-send`.

```sh
git clone https://github.com/byliu-labs/agent-storage-guard.git
cd agent-storage-guard
uv tool install .
agent-storage-guard --help
```

Keep this checkout to install the bundled skill. For future updates, update the checkout and run `uv tool upgrade agent-storage-guard --reinstall`. This repository does not publish a PyPI package.

## Copy this to your agent

```text
Install https://github.com/byliu-labs/agent-storage-guard on this computer.
1. Check the platform and prerequisites. Read README.md and AGENTS.md in the
   checkout before running setup. Use a new checkout or reuse a clean one;
   preserve existing local changes. Install with uv tool install . and verify
   agent-storage-guard --help. Ask before installing missing system tools.
2. Install the entire skills/sweep-computer-disk directory (including scripts,
   references and agents) in this agent's supported skills directory. For Codex,
   use ${CODEX_HOME:-$HOME/.codex}/skills/sweep-computer-disk; for Claude Code,
   use ~/.claude/skills/sweep-computer-disk. If it already exists, compare it
   and preserve a backup outside the discovery directory before updating.
   For other agents, check their skill format/location first. Confirm the
   skill is discoverable; restart the agent session if needed.
3. Ask which folders to monitor. Initialize with explicit --root paths, then
   run monitor scan and monitor report --open. Explain coverage gaps.
4. Offer scheduled monitoring; activate it only after I approve. Explain any
   notification/compiler requirements. Do not rewrite global agent hooks.
5. Show a guarded test/build example. Do not scan protected app data, upload
   reports, delete files, empty Trash, or perform cleanup during installation.
Report the installed version, skill location, monitored roots and whether
scheduled monitoring is active.
```

To install the skill manually, copy the **whole** [`skills/sweep-computer-disk`](skills/sweep-computer-disk) folder into your agent's skills directory. Keep any previous version before replacing it. Invoke it with `$sweep-computer-disk` in Codex, `/sweep-computer-disk` in Claude Code, or your agent's equivalent.

## Start monitoring

After choosing the folders you want measured:

```sh
agent-storage-guard monitor init --root /absolute/path/to/projects
agent-storage-guard monitor scan
agent-storage-guard monitor report --open
```

Repeat `--root` for more folders. Omit it to use existing development, agent and cache defaults. The report shows current pressure, changes, storage areas and safe next actions. **Copy investigation prompt** hands the evidence to your agent; use the sweep skill for the wider audit.

For scheduled monitoring, after reviewing the configuration:

```sh
agent-storage-guard monitor install --activate
```

On macOS, capacity/memory checks default to every five minutes and folder scans to hourly. See [monitoring](docs/monitoring.md) for configuration, platform limits and migration from the earlier backup monitor. Unattended hosts can opt into [Feishu/Lark alerts](docs/remote-alerts.md) and [custom CI storage roots](docs/ci-host-monitoring.md).

## Guard a command

```sh
agent-storage-guard run --estimate-gib 2 --label project-tests -- uv run pytest
agent-storage-guard status
agent-storage-guard reconcile
```

All sessions must share the same state directory and policy. Defaults allow two runs and reserve 20 GiB free space. Only commands launched through the guard are covered; writes outside their assigned temporary directory are outside its scratch budget.

## Safety and privacy

- Monitoring is read-only. It never automatically deletes files or stops applications.
- The guard stops its managed command on pressure and moves validated owned scratch to Trash (macOS) or retained storage (Linux). **Moving to Trash does not free disk space.** Permanent disposal needs a separate decision.
- This is a watchdog for cooperative tools, not a sandbox or hard filesystem quota.
- Reports, history, receipts and copied investigation prompts contain local paths and metadata. Keep them private; review before sharing. Commands and environment values are not stored in receipts; keep secrets out of user labels.
- Local state defaults to `~/.asg`; the report is `~/.asg/monitor/report.html`. No hosted service or remote report assets are required.

See [guard behavior, recovery and exit codes](docs/command-guard.md), [integration examples](docs/integrations.md), [measurement limits](docs/measurement.md), and the [publication privacy review](docs/privacy-review.md).

## Development

```sh
uv sync --locked
uv run pytest --collect-only -q
uv run pytest -q
uv build
```

Tests use small real files/processes; CI covers macOS and Linux with Python 3.11 and 3.13. See [AGENTS.md](AGENTS.md), [reuse decisions](docs/reuse.md), [acknowledgements](ACKNOWLEDGEMENTS.md) and [third-party notices](THIRD_PARTY_NOTICES.md).
