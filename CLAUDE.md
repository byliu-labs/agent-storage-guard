# Agent Storage Guard

Read [AGENTS.md](AGENTS.md). The package is a headless workstation monitor and managed-command guard with a standard-library runtime. CLI inputs flow to the shared registry, owned process controller and native measurement adapter; retirement is a checked same-volume rename and receipts preserve outcomes.

Component map: `cli.py` parses commands; `guard.py` supervises runs; `run_registry.py` serializes admission/recovery and transitions; `ownership.py` validates private state and filesystem identity; `processes.py` tracks process identity and group lifetime; `measurement.py` invokes native bounded `du` and reads physical free space.

Monitoring flows through `monitor.py`, native `folder_scan.py`/`memory_pressure.py`, `monitor_store.py`, pure `alerts.py`, and `notifications.py`. `installation.py` owns reversible launchd replacement; `workstation_report.py` presents evidence and actions. Keep one monitor history authority and reuse native Git/du rather than implement another scanner or worktree manager.

Use `uv run pytest`, `uv build` and the real CLI. When changing a lifecycle rule, exercise normal completion and its interruption/crash boundary. Never infer disposal authority from dirtiness, time or a directory prefix.
