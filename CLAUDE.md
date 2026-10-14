# Agent Storage Guard

Read [AGENTS.md](AGENTS.md). The package is a headless managed-command guard with a standard-library runtime. CLI inputs flow to the shared registry, owned process controller and native measurement adapter; retirement is a checked same-volume rename and receipts preserve outcomes.

Component map: `cli.py` parses commands; `guard.py` supervises runs; `run_registry.py` serializes admission/recovery and transitions; `ownership.py` validates private state and filesystem identity; `processes.py` tracks process identity and group lifetime; `measurement.py` invokes native bounded `du` and reads physical free space.

Use `uv run pytest`, `uv build` and the real CLI. Do not add another scanner, worktree manager, MCP server or history database to this extraction. When changing a lifecycle rule, exercise normal completion and its interruption/crash boundary. Never infer disposal authority from dirtiness, time or a directory prefix.
