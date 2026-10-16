---
name: sweep-computer-disk
description: Audit macOS disk and memory pressure, classify large cleanup candidates by durability and redownload cost, and propose an approval-scoped cleanup without deleting anything first.
---

# Sweep Computer Disk

Use this skill when the user asks what is consuming space or wants stale files cleaned from a Mac. Treat "memory space" as potentially ambiguous: report both RAM/swap pressure and persistent disk capacity, then make clear that deleting files directly reclaims disk, not RAM.

## Pair with Agent Storage Guard

Agent Storage Guard provides local monitoring, growth history and scratch budgets for wrapped commands. Use `agent-storage-guard monitor report --open` or the overview's **Copy investigation prompt** as starting evidence when installed. Refresh that evidence; configured-root monitoring is not a full workstation audit. This skill also works independently on macOS and requires no guard installation.

Run bundled helper paths relative to this skill directory. Discovery needs Python 3 and Git; optional dependency retirement needs macOS, zsh, Git, `gh` authentication and `lsof`. Keep generated ledgers, plans and reports private and outside the source checkout.

## Safety boundary

- Audit first. Do not delete, prune, uninstall, stop processes, empty Trash, or alter Git worktrees before presenting an itemized proposal and receiving approval.
- Inspect metadata only: paths, sizes, dates, Git state, application versions, and native dry-run output. Do not read file contents or expose credentials.
- Treat private application data (including `~/Library/Application Support/`), credential stores, and user-designated production paths as protected. Inspect them only with explicit permission.
- Treat `Operation not permitted` and `Permission denied` as coverage gaps, not zero usage.
- If approved, keep the exact approved scope. Prefer timestamped Trash staging with a manifest and restore script for ordinary files. Use native narrow cleanup commands for package managers and containers; never substitute a broad prune.

## Audit procedure

1. Record `df -h`, `vm_stat`, `sysctl vm.swapusage`, APFS volume usage, and local snapshot names.
2. Apply the user's thresholds. If none are provided, focus on files over 50 MB and directories over 200 MB.
3. Complete the automatic discovery and coverage procedure below before selecting cleanup targets. Start with bounded top-level measurements of the writable data volume, home directory, `/Applications`, `/opt`, and `/private`. Drill into only threshold-crossing directories. Avoid parallel full-tree scans; they can exhaust file descriptors. Continue authorized scans even when they run longer than five minutes; report progress and any genuine resource or access limits. Ask before heavy local/cloud transfers or spending money, not solely because time has elapsed.
4. Inspect common high-value areas separately: Downloads, Desktop archives, temporary directories, developer caches, model caches, package caches, application update staging, Docker, Xcode simulators, and project worktrees.
5. For individual large files, report size, modification date, and path. For folders, report their measured size and largest children when useful.
6. Classify each candidate:
   - Durable win: stale duplicate, completed temporary artifact, installer/update payload matching an installed version, or a Git worktree that passes every whole-worktree gate below.
   - Regenerable but costly: dependency, browser, build, or model cache. State likely regrowth and redownload cost; normally keep recent model and package caches.
   - User data or unknown: archives, recordings, databases, Docker volumes, app profiles, messages, and protected paths. Keep unless ownership and redundancy are proven.
7. For worktrees, prefer dependency trimming over whole-worktree cleanup:
   - Measure ignored `.venv` and `node_modules` directories separately and propose them first as rebuildable candidates. State their combined logical size and reinstall cost. On APFS, first inspect how they were created: `cp -c`/`cp -Rc`, uv's cache linking, pnpm's content-addressed store, and other clone/hardlink mechanisms can make `du` double-count shared physical extents. Never present summed `du` output as guaranteed reclaim. Moving files to Trash does not free space. Record volume free space before staging and, only if the user later empties the exact Trash bundle, again afterward; report that observed delta separately from logical bytes staged.
   - Do not generalize this rule to every ignored directory. Build outputs, registries, datasets, and other ignored paths may contain valuable local state; classify them separately and keep them unless proven reproducible.
   - Dependency trimming does not remove the worktree and therefore does not require the whole-worktree 14-day age gate. It still requires a clean worktree, no live process whose CWD is inside it, fresh exact merge proof, no open PR contradiction, exact-path approval, and a final preflight. Keep dependencies for dirty, active, open-PR, closed-unmerged, or merge-uncertain worktrees.
   - A whole worktree is eligible only when it is clean, inactive, conclusively merged, and outside a 14-day safety window. Verify both the HEAD commit timestamp and newest filesystem modification time, excluding the `.git` administrative link; if either is within the last 14 days, preserve it.
   - Refresh the relevant remote refs before merge verification. Require unambiguous evidence for the exact HEAD or branch: the fetched integration branch contains HEAD, or the Git provider reports its exact PR as merged. Also verify there is no open PR or contradictory branch state. Stale ancestry, a closed-but-unmerged PR, missing provider access, or any uncertainty means preserve the worktree.
   - Present the merge proof, activity timestamps, size, and exact path for approval. After approval, stage the directory in timestamped Trash with a manifest and a restore procedure that can reconstruct Git worktree registration; do not prune registration first.
   - Preserve dirty, advanced, active, recent, detached-with-unclear-provenance, or otherwise unknown worktrees.
   - For a large dependency-only batch, follow [references/worktree-dependency-reclaim.md](references/worktree-dependency-reclaim.md). It defines the evidence table, exact-list handoff, all-or-nothing preflight, reversible Trash bundle, and physical-reclaim measurement.
8. Use dry runs such as `brew cleanup -s -n` and `docker system df -v`. Recommend only named unused images or narrowly proven artifacts; never recommend unreviewed volume or system pruning.

## Automatic discovery and coverage

Discovery is the agent's responsibility. Do not ask the user to enumerate folders,
restrict the audit to paths they happen to mention, or treat an initial shortlist
as complete. User-named paths are additional checks, not the discovery strategy.

- Enumerate home-directory children, including hidden directories, and classify
  each as measured, protected, inaccessible, live application/tool state, or queued
  for a deeper pass. Review loose files and standalone folders separately from
  caches and repositories. Measure grouped categories as well as single folders;
  many small worktrees or backups can exceed the user's threshold together.
- Discover Git markers across local development locations, hidden agent workspaces,
  Documents/Desktop project locations, and sibling project folders. Run
  `scripts/discover-worktrees.py` with its default home scan; add roots found on
  other local volumes or outside home. Read its exclusions and errors. A bounded
  scan is acceptable only with explicit coverage gaps, never a claim of complete
  discovery. Do not follow cloud placeholders or scan protected production data.
- For every distinct Git common directory found, run `git worktree list --porcelain`.
  Add every registered path to the census, including sibling, external, hidden,
  detached, missing, and prunable worktrees. Recursively process newly discovered
  common directories until the census stops growing. Never assume worktrees live
  inside their repository or in a directory named `worktrees`.
- Separately inventory orphan/unregistered checkouts, standalone clones, dated
  backup roots, quarantine roots, prior cleanup bundles, and project-level loose
  `output/`, report, screenshot, archive, and temporary folders. Git registration
  alone cannot discover these. Follow up every unclassified material category.
- Save a coverage ledger with discovered roots, common directories, registered and
  unregistered paths, measured sizes, classifications, exclusions, errors, and
  unresolved items. Before handoff, reconcile the filesystem inventory with Git's
  registrations and the proposal; explain every material omitted category.

### Coverage checkpoint before a final proposal

For a workstation audit, account for every direct sibling under material project
families and every discovered material storage root. Reconcile enumerated paths
against unique ledger rows: record path, measurement time, nullable bytes,
disposition and reason; show enumerated/classified counts and unresolved paths.
Separate working checkouts from installed tools, generated fixture repositories,
archives, prior Trash registrations and missing paths. Inspect large stable areas
and older worktrees as well as recent growers; measure dependency-only candidates
separately. A monitor's configured-root coverage is not workstation coverage.
Honor a deliberately narrow user scope and label that result as limited.

Compare the proposed accounting size with the reserve shortfall. If safe candidates
are insufficient, continue discovery within scope or explain the preservation and
access limits; a small temporary-file shortlist is not proof of audit completion.
Keep this checkpoint's reconciled ledger with the proposal, rather than claiming
coverage from a Git census or a few inspected examples.

Use bounded structured evidence or visible report text when reading local reports;
avoid dumping embedded styles/scripts. Checkpoint each scanned root, serialize
large-tree scans, and stop repeated expensive queries on a timed-out repository.
Keep failed sizes and dates null with a reason: unknown age cannot satisfy an age
gate. Review existing prevention tools and actual invocation coverage before
proposing replacements. Distinguish proposed prevention from installed protection.

Reusable instructions describe these patterns and checks. Derive concrete target
paths from current discovery or report data; keep machine-specific paths, counts,
timestamps, approved lists and restore commands in that run's private artifacts.

Preserve unfinished research and LLM-generated reports regardless of age. Treat
ignored/untracked outputs as project work until completion or verified redundancy
is established. GitHub commits do not back up ignored reports, databases, local
configuration, or uncommitted work. Where the user authorizes archive preservation,
copy coherent sets to the SSD by subject, verify all file bytes, then stage only the
local copies in Trash. State any pre-existing missing files explicitly.

For worktree reclamation, a user's explicit allowance for old unmerged but published
branches overrides the default merged-only rule. Still require the 14-day window,
clean/inactive state, fresh proof that GitHub contains every local commit, no open
PR, and separate preservation of local-only artifacts. Never equate a same-named
remote branch with exact commit coverage. Preserve Git administration and provide
restoration that repairs or moves registration; never prune first.

When organizing an SSD, inventory existing subject roots before introducing another
hierarchy. Choose one canonical project browsing root, retain compatibility shortcuts
for live consumers, and provide a visible guide and old-to-new index. Map each Trash
bundle ID to the actual SSD folder. Verify that representative reports can be found
through this guide, not merely that the copy manifest parses.

## Preserve access when archiving or offloading

Organization, backup creation, and disk reclamation are separate outcomes. Moving
files within a drive or adding shortcuts does not reclaim capacity, and an archive
copy is not restore proof. Preserve the existing approval and verification gates.

When the requested cleanup includes keeping user data in an archive, make that
archive retrievable by project or subject. Do not bury its only useful copy under
`Review Copies/<date>/Downloads`. Record source paths and dates in the manifest,
keep coherent technical sets intact, and verify a few real file-finding examples.
Use a separate content-organization workflow when semantic organization is actually requested;
the metadata-only cleanup audit does not authorize content inspection.

Check ignored `docs/`, `output/`, data, receipts, and reports separately from
rebuildable dependencies. A clean Git status, old timestamp, or equal file size
does not establish that these files are disposable or backed up. Preserve valuable
local artifacts and their provenance before proposing worktree removal.

## Report format

Lead with disk capacity and RAM/swap pressure. Then provide:

- Recommended candidates with exact scope, estimated reclaim, evidence, reversibility, and redownload/regrowth risk.
- Keep list for large but valuable or costly-to-redownload data.
- Needs-decision list for user archives, recordings, unused applications, and unidentified databases/volumes.
- Coverage gaps caused by macOS privacy controls or stopped long-running scans.
- A total conservative reclaim estimate that does not double-count nested paths.

End by asking the user to approve specific candidate groups. Do not treat that question as authorization to remove anything not explicitly listed.
