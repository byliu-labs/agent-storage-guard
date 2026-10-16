"""Audit scope and starting points, shared by the human overview and agent brief."""
from pathlib import Path


def plan(rows: list[dict], specs: list[dict]) -> dict:
    paths = sorted({item["path"] for item in specs})
    by_spec = {item["path"]: item for item in specs}
    by_path = {row["path"]: row for row in rows}
    roots = [{**by_spec[path], "coverage": by_path.get(path, {}).get("coverage", "unknown"),
              "bytes": by_path.get(path, {}).get("bytes"),
              "observed_at": by_path.get(path, {}).get("observed_at")}
             for path in paths]
    # Show the shallowest measured branches below configured roots. A parent and
    # its children are alternative drill-down levels, not additive candidates.
    frontier = [p for p in paths if not any(str(a) in paths for a in Path(p).parents)]
    candidates = [r for r in rows if r["bytes"] is not None and any(
        Path(root) in Path(r["path"]).parents for root in frontier)]
    measured = {r["path"] for r in candidates}
    branches = [r for r in candidates if not any(str(a) in measured for a in Path(r["path"]).parents)]
    covered = {root for root in frontier if any(Path(root) in Path(r["path"]).parents for r in branches)}
    branches += [by_path[root] for root in frontier if root not in covered
                 and root in by_path and by_path[root]["bytes"] is not None]
    ordered = sorted(branches, key=lambda r: (r["coverage"] != "complete", -r["bytes"], r["path"]))
    return {"audit_complete": False, "scope": "configured roots only", "roots": roots,
            "starting_points": ordered[:8], "additional_starting_points": max(0, len(ordered) - 8)}


PROCEDURE = (
    "Audit the workstation, not just the examples below. Read textarea#agent-context in the saved HTML "
    "with an HTML parser and JSON decoder; it contains all configured roots, folder evidence and scan gaps. "
    "Do not dump embedded scripts/styles. Refresh sizes and activity: this is a snapshot, not current truth. "
    "Bracketed virtual groups are not literal paths; enumerate their inspect_path parent.",
    "Before proposing cleanup: enumerate all direct siblings, including hidden folders, under each material "
    "project family below. Discover Git markers across local development and hidden agent workspaces; "
    "reconcile each Git common directory's worktree registrations, standalone clones, loose archives/output "
    "folders and missing/Trash entries. Count installed tools and generated fixture repositories separately "
    "from working checkouts. Inspect older worktrees as well as recent growers. Separately measure "
    "dependency-only .venv/node_modules candidates; do not treat ignored reports, configuration or databases as dependencies.",
    "Expand discovery beyond monitored roots: bounded metadata-only home/data-volume measurements, Downloads, "
    "application/runtime data, caches and temp roots. Exclude protected production data, credential stores and "
    "cloud placeholders. Scan one large tree at a time, checkpoint each root, bound Git queries and retain "
    "partial/denied/timeouts as gaps. Unknown size and unknown age stay null, never zero or cleanup eligibility.",
    "Completion gate before presenting a final cleanup proposal: save a coverage ledger with one disposition "
    "per discovered material root and every project sibling (measured/keep/protected/partial/inaccessible/unresolved); "
    "reconcile enumerated counts against ledger rows and Git registrations. Report gaps and why major areas stay. "
    "Include path, measured bytes/time, disposition and reason; verify enumerated count equals classified count "
    "and list unresolved paths. A small temp shortlist does not close the reserve shortfall; continue discovery or explain why further "
    "cleanup is unsafe. Compare candidate accounting bytes with the shortfall without claiming APFS physical reclaim.",
    "Worktree safety: require clean/inactive state, fresh exact HEAD merge proof and no open PR contradiction "
    "for dependency trimming. Whole-worktree removal additionally requires the 14-day window for both HEAD and "
    "filesystem changes. Preserve ignored/untracked local evidence separately; retain Git registration and a "
    "tested restore path. If any proof is unavailable, keep it and explain. Review existing prevention tooling "
    "and actual test invocation paths before proposing another guard; separate proposed fixes from installed ones.",
)
