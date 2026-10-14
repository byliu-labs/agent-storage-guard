# Integrating existing tools

## Direct test commands

Use the same installed guard and state directory for every simultaneous session on a workstation. A separate state directory creates a separate reservation coordinator. Do not use a per-worktree state directory when protecting shared capacity.

```sh
agent-storage-guard run --estimate-gib 2 --label project-tests -- uv run pytest
agent-storage-guard run --estimate-gib 2 --label video-tests -- npm test
```

Estimates cover scratch and projected whole-run growth. Only the guard-owned temporary root is measured against the scratch ceiling. Repositories, caches and build outputs elsewhere remain outside that ceiling; whole-volume free-space checks provide a separate backstop.

Permission/error-injection tests that make scratch unreadable can fail the allocation check. Such a suite needs its own reviewed resource boundary rather than disabling this guard’s coverage checks. This wrapper is intended for cooperative tools whose scratch can be measured.

## Worktrunk

Worktrunk already owns worktree creation, lifecycle hooks and COW cache reuse. Keep those operations there. Wrap the concrete test/build command run by a hook or command alias with `agent-storage-guard run ... -- COMMAND`. Do not wrap a long-lived agent session with a tiny test estimate, and do not treat a pre-start check by itself as runtime enforcement.

## Delegate and other agent runners

Wrap the ordinary test command inside an agent run. Delegate's advanced command-prefix/safe-mode binary identity rules need their own compatibility validation before wrapping its harness launcher. This version does not install or edit another tool's configuration.

## Existing monitoring tools

Use existing capacity/history tools alongside this guard. `status` and receipts expose local run ownership for investigation. A read-only MCP connector may consume them in a future independently reviewed integration; no new MCP service or duplicate folder-history store is installed here.

Nothing in this project proves that a directory outside its own registry is safe to remove. Use each worktree manager's reviewed lifecycle and preserve unfinished outputs.
