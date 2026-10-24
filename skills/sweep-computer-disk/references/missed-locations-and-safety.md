# Missed locations and safe reclamation

Read for developer/CI storage, rapid regrowth, or before moving shared Git or
environment storage. Examples below are relative to the named project, runner,
home, or audit root. Discover actual roots on the current host; keep machine
paths and approved target lists in that run's artifacts.

## Find copies outside the obvious roots

- Under each discovered project family, enumerate every direct sibling, hidden
  child and nested `worktrees/` directory. Include standalone clones, exported
  source without `.git`, dated test snapshots and archive files. A Git worktree
  census misses transferred copies; a scan of only the primary checkout misses
  sibling checkouts.
- On CI hosts, inventory both runner-relative `_work/` and independently created
  project/scratch roots. Discover hidden guard-worktree directories, fixtures and
  quarantine roots from directory metadata and relevant producer scripts.
- Separate runner installations from job resources. A finished job does not prove
  that a shared workspace, tool cache or database cluster is unused. Establish run
  ownership, completion and current process use before selecting disposable data.
- Classify leftover project shells separately: source may already be gone while
  ignored configuration, `docs/`, `output/`, screenshots and receipts remain.
  Measure those children rather than treating the old folder name as a full
  duplicate checkout. Preserve their coherent evidence sets.

## Explain the bytes and the growth

- Within large project roots, measure Python environments such as `.venv/`, Node
  dependencies such as `node_modules/`, build/test products, Git stores, model
  downloads, datasets/media, archives, databases and reports separately. Aggregate
  repeated small copies by category. Use directory structure and native tool
  metadata as well as extensions; media libraries and database stores are often
  packages or extensionless trees. Count each subtree once and retain an unknown
  category where classification is uncertain.
- Consult Agent Storage Guard or the installed equivalent before another broad
  scan. Read configured roots, scan errors and snapshot timestamps. Retry failed
  roots sequentially and checkpoint completed measurements. Report inaccessible
  or timed-out roots as coverage gaps; monitor coverage is not full-disk coverage.
- For requested 24/48-hour comparisons, use snapshots nearest those intervals and
  report the actual elapsed hours, per-root deltas and intervening cleanup. A net
  decrease after deletion does not measure gross writes. Inspect large stable
  areas too; a recent-growth chart cannot find every reclaim opportunity.
- Re-enumerate affected roots before handoff. Record newly created test copies,
  recreated caches and new application metadata separately from the approved old
  payload. New files at a former target path are not covered by its old approval.

## Shared Git storage and environment consumers

- Resolve `git rev-parse --git-dir`, `git rev-parse --git-common-dir` and
  `git worktree list --porcelain` for each distinct store. Map retained checkouts
  to the storage they need, including stores inside another clone selected for
  cleanup. Keep that store when a retained checkout depends on it. A clean,
  merged owner checkout does not establish that its Git administration is unused;
  remote commits do not preserve local branches, stashes, reflogs or indexes.
- Resolve symlinks and known environment references from retained consumers to
  their donors. Preserve in-use donors even inside an otherwise finished tree.
  Record already-missing targets rather than silently recreating dependencies.
  Before replacing duplicate environments with references, verify runtime,
  platform, dependency graph and tool support; matching lockfiles do not prove
  environment portability. File/archive deduplication requires byte verification,
  with a retained canonical copy and checked consumers.
- When moving linked worktrees, repair registrations together per common store
  and validate both moved and retained checkouts after publication. Compare HEAD,
  branch, status and retained working-file state with the baseline. Exercise any
  changed staging helper on a real Git fixture with a retained dirty checkout
  sharing the candidate's store before touching live work.

## Activity, Trash and application libraries

- Interpret process handles by type and access mode. A verified Finder numeric,
  read-only directory handle alone is passive browsing. Preserve targets with
  working-directory, executable, regular-file or write handles; keep ambiguous
  cases. Repeat activity and identity checks immediately before each move.
- Save manifests, approval scope, Git mappings and receipts outside Trash before
  publishing a bundle. Another actor may empty Trash during the sweep; keep all
  retained consumers independent of its contents. Recheck bundle existence before
  offering restoration and label stale restore instructions as historical.
- When explicitly authorized to remove a local cloud-backed photo library,
  confirm synchronization is off on that exact host before moving the whole
  package. Sync-off confirmation does not itself authorize deletion. Preserve
  newly recreated metadata and unrelated cloud content. An administrator denial
  is an access limit; report it and honor later instructions to leave apps intact.

## Prevent recurrence at the producer

Trace which launcher creates each disposable class. GitHub job hooks cover their
jobs; independently transferred SSH/manual snapshots need cleanup in their own
launcher. Recommend run-owned directories and manifests, compact receipts outside
the disposable tree, completion cleanup, and conservative recovery of interrupted
runs. Preserve runner agents, credentials, shared toolchains and live databases.

Prefer native package-cache maintenance with concurrency checks over deleting
shared cache internals. State download/regrowth costs. Keep a proposed lifecycle
distinct from an installed policy: recurring disposal needs approved resource
classes and retention, plus validation on real successful and failed runs.
