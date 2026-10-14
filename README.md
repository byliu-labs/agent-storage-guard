# Agent Storage Guard

A small guard for managed test and development commands: reserve storage before starting, own temporary resources, and preserve recovery evidence.

This repository is being extracted from a tested local prototype. The initial foundation is documentation only; functional changes are reviewed through pull requests. It is not a disk visualizer, worktree manager, or security sandbox.

The first slice will supply shared admission, bounded scratch accounting, child-process ownership, crash reconciliation and reversible retirement. Existing monitoring and worktree tools remain separate. A polling guard is best-effort; hard quotas require an enforced storage boundary.

See [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md) for prior work and [AGENTS.md](AGENTS.md) for development rules.
