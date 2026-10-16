# Worktree dependency reclaim

Read this reference when ignored `.venv` or `node_modules` directories across Git worktrees form a material cleanup batch. The objective is to reclaim rebuildable dependencies without deleting source, Git metadata, local diffs, archives, or unrelated ignored data.

## 1. Explain what is duplicated

Before proposing a large number, inspect the repository's worktree creation scripts and dependency tooling.

- Group worktrees by repository and lockfile digest (`uv.lock`, `package-lock.json`, `pnpm-lock.yaml`, or equivalent). Report the number of materialized environments versus distinct dependency graphs.
- Check for APFS clonefile commands such as `cp -c` or `cp -Rc`. Check whether Python environments are materialized from uv's cache or Node packages from a content-addressed store.
- Treat `du` as logical/accounting size. Clonefile and hardlink-backed trees can share physical storage even when each path reports its full size. Do not promise that deleting N GiB of logical trees will increase free space by N GiB.
- Capture `df -k /System/Volumes/Data` and `diskutil info /System/Volumes/Data` before staging. A move into Trash stays on the same volume and normally reclaims no space. If the user later empties the exact Trash bundle, capture the measurements again afterward. Label that change as observed volume free-space delta; concurrent writes, snapshots, and delayed APFS accounting prevent exact attribution.

## 2. Build the exact candidate table

Refresh each repository's remote once and query provider PR state once per repository. Do not make one network request per dependency directory.

Include a dependency only when all conditions hold:

1. The registered worktree exists and is clean.
2. No live process has a current working directory at or below the worktree.
3. The current branch and HEAD are recorded.
4. The exact HEAD is contained by the freshly fetched integration ref, or the provider reports an exact matching PR as merged.
5. No exact matching PR is open and there is no contradictory branch state.
6. The target is a real directory, not a symlink; its basename is exactly `.venv` or `node_modules`; and it is below the recorded worktree.

Ancestry uncertainty, provider failure, a closed-unmerged PR, dirty state, a live process, or a changed branch excludes the worktree. The 14-day gate applies to removing a whole worktree, not to trimming its rebuildable dependencies after the checks above.

For squash merges, a branch-name match is insufficient: require the merged PR's provider-reported head OID to equal the worktree HEAD. A reused or advanced branch can otherwise make unrelated local work look merged.

Write a TSV before requesting or acting on approval. Use one row per exact dependency directory with at least:

```text
candidate_id  size_kib  kind  merge_proof  pr_number  pr_url  branch  head  head_committed_at  integration_ref  integration_sha  repo_slug  repo_path  worktree_path  dependency_path
```

Use explicit placeholders such as `-` for absent optional values. Empty tab-separated fields can collapse in shell readers and silently shift safety-critical columns. Sort paths deterministically, assign stable IDs, reject duplicate targets, and total without double-counting nested paths.

## 3. Generate a guarded staging script

The user-facing script must support `--dry-run` and `--apply`; default to dry-run. It reads the saved TSV rather than rediscovering broader targets.

Before moving the first directory, preflight the entire list again:

- validate the header, field count, sequential IDs, unique target paths, safe basenames, containment, directory type, and recorded size;
- verify every worktree is still clean and has no live process CWD;
- verify branch and HEAD are unchanged;
- fetch each repository once, preload its PR states once, and re-prove merge status with no open-PR contradiction;
- fail the whole run if any row changed. Do not partially stage a batch whose preflight failed.

When other agents may be active, bind approval to the SHA-256 digest of the exact TSV and acquire an atomic per-plan lock before apply. Recheck live CWDs and every target immediately before its move. Prewrite the complete manifest and restore script, journal each successful move, and roll already-moved items back in reverse order after a failure. Keep the failed bundle and journal for recovery; never delete them. These checks narrow the race but cannot prevent a non-cooperating process from starting after the last check.

`--apply` moves only the listed directories into a new timestamped Trash bundle. It writes a manifest mapping every original path to its staged path and creates a `restore.sh --dry-run|--apply`. Never run `rm`, empty Trash, prune worktree registration, or interpret dependency approval as approval to move the worktree.

## 4. Validate and report

Run syntax checks and the complete `--dry-run`. Confirm every original exists after dry-run and no ZIP, archive, whole-worktree root, or path outside the table is targeted. Report:

- candidate directory and worktree counts;
- logical bytes represented by the exact list;
- lockfile-group counts and detected clone/cache mechanisms;
- the command the user can run and the restoration behavior.

After `--apply`, verify every listed source is absent, every manifest destination exists, and restore dry-run passes. Report logical bytes staged, but do not call them reclaimed while the bundle remains in Trash. If the user later empties that exact bundle, report the before/after free-space delta. A much smaller physical delta than the logical total is expected when APFS clone extents or package-cache objects remain shared.

For one merge-proven linked worktree at a time, use `scripts/retire-worktree-dependencies.zsh`: run `inventory`, approve the printed plan digest, run `dry-run`, then pass that exact digest to `apply`. It excludes the primary checkout and never removes worktree registration.
