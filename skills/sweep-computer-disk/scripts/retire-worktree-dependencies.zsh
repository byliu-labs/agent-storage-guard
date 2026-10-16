#!/bin/zsh
set -eu

usage() {
  print -u2 'Usage:'
  print -u2 '  retire-worktree-dependencies.zsh inventory --worktree PATH --plan PATH [--base origin/main] [--repo-slug OWNER/REPO]'
  print -u2 '  retire-worktree-dependencies.zsh dry-run --plan PATH'
  print -u2 '  retire-worktree-dependencies.zsh apply --plan PATH --approved-sha256 SHA256'
}

[[ $# -ge 1 ]] || { usage; exit 64; }
command_name=$1
shift
worktree=''
plan=''
base_ref=''
repo_slug=''
approved_sha=''
while (( $# > 0 )); do
  case "$1" in
    --worktree|--plan|--base|--repo-slug|--approved-sha256)
      option=$1
      shift
      (( $# > 0 )) || { usage; exit 64; }
      case "$option" in
        --worktree) worktree=$1 ;;
        --plan) plan=$1 ;;
        --base) base_ref=$1 ;;
        --repo-slug) repo_slug=$1 ;;
        --approved-sha256) approved_sha=$1 ;;
      esac
      ;;
    *) usage; exit 64 ;;
  esac
  shift
done

has_unsafe_path_chars() {
  [[ "$1" == *$'\t'* || "$1" == *$'\n'* ]]
}

typeset -a live_cwds
load_live_cwds() {
  live_cwds=()
  local output line
  output=$(lsof -a -d cwd -Fn 2>/dev/null || true)
  for line in ${(f)output}; do
    [[ "$line" == n/* ]] && live_cwds+=("${line#n}")
  done
}

has_live_cwd() {
  local root=$1 cwd
  for cwd in $live_cwds; do
    [[ "$cwd" == "$root" || "$cwd" == "$root"/* ]] && return 0
  done
  return 1
}

infer_base_ref() {
  local root=$1 symbolic
  symbolic=$(git -C "$root" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null || true)
  if [[ -n "$symbolic" ]]; then
    print "$symbolic"
  elif git -C "$root" show-ref --verify --quiet refs/remotes/origin/main; then
    print refs/remotes/origin/main
  elif git -C "$root" show-ref --verify --quiet refs/remotes/origin/master; then
    print refs/remotes/origin/master
  else
    return 1
  fi
}

infer_repo_slug() {
  local root=$1 remote slug
  remote=$(git -C "$root" remote get-url origin)
  case "$remote" in
    git@github.com:*) slug=${remote#git@github.com:} ;;
    https://github.com/*) slug=${remote#https://github.com/} ;;
    *) return 1 ;;
  esac
  print "${slug%.git}"
}

fetch_base() {
  local root=$1 ref=$2 branch
  [[ "$ref" == refs/remotes/origin/* ]] || { print -u2 "Unsafe base ref: $ref"; return 1; }
  branch=${ref#refs/remotes/origin/}
  git -C "$root" fetch origin "+refs/heads/$branch:$ref"
}

load_pr_evidence() {
  local slug=$1 row pr_number pr_state pr_branch pr_oid pr_url key
  typeset -gA open_branch merged_pr_number merged_pr_url
  open_branch=()
  merged_pr_number=()
  merged_pr_url=()
  rows=$(gh pr list --repo "$slug" --state all --limit 2000 \
    --json number,state,headRefName,headRefOid,url \
    --jq '.[] | [.number,.state,.headRefName,.headRefOid,.url] | @tsv')
  for row in ${(f)rows}; do
    IFS=$'\t' read -r pr_number pr_state pr_branch pr_oid pr_url <<< "$row"
    [[ "$pr_state" == OPEN ]] && open_branch[$pr_branch]=yes
    if [[ "$pr_state" == MERGED ]]; then
      key="$pr_branch|$pr_oid"
      merged_pr_number[$key]=$pr_number
      merged_pr_url[$key]=$pr_url
    fi
  done
}

verify_registered_nonprimary() {
  local root=$1 line first='' found=no
  entries=$(git -C "$root" worktree list --porcelain)
  for line in ${(f)entries}; do
    [[ "$line" == worktree\ * ]] || continue
    registered_path=${line#worktree }
    [[ -n "$first" ]] || first=$registered_path
    [[ "$registered_path" == "$root" ]] && found=yes
  done
  [[ "$found" == yes ]] || { print -u2 "Not a registered worktree: $root"; return 1; }
  [[ "$root" != "$first" ]] || { print -u2 "Primary checkout is not eligible: $root"; return 1; }
}

prove_merge() {
  local root=$1 branch=$2 head=$3 ref=$4 key
  [[ -z ${open_branch[$branch]-} ]] || { print -u2 "Branch has an open PR: $branch"; return 1; }
  if git -C "$root" merge-base --is-ancestor "$head" "$ref"; then
    print $'ancestry\t-\t-'
    return 0
  fi
  key="$branch|$head"
  [[ -n ${merged_pr_number[$key]-} ]] || { print -u2 "No exact merge proof for $branch at $head"; return 1; }
  print "merged_pr\t${merged_pr_number[$key]}\t${merged_pr_url[$key]}"
}

inventory() {
  [[ -n "$worktree" && -n "$plan" && -z "$approved_sha" ]] || { usage; exit 64; }
  [[ ! -e "$plan" ]] || { print -u2 "Plan already exists: $plan"; exit 1; }
  worktree=$(cd "$worktree" && pwd -P)
  has_unsafe_path_chars "$worktree" && { print -u2 'Unsafe worktree path'; exit 1; }
  verify_registered_nonprimary "$worktree"
  [[ -z "$(git -C "$worktree" status --porcelain=v1)" ]] || { print -u2 "Dirty worktree: $worktree"; exit 1; }
  load_live_cwds
  has_live_cwd "$worktree" && { print -u2 "Live process CWD in worktree: $worktree"; exit 1; }
  branch=$(git -C "$worktree" symbolic-ref --quiet --short HEAD) || { print -u2 'Detached worktree'; exit 1; }
  head=$(git -C "$worktree" rev-parse HEAD)
  [[ -n "$base_ref" ]] || base_ref=$(infer_base_ref "$worktree") || { print -u2 'Cannot infer base ref'; exit 1; }
  [[ "$base_ref" == origin/* ]] && base_ref="refs/remotes/$base_ref"
  [[ -n "$repo_slug" ]] || repo_slug=$(infer_repo_slug "$worktree") || { print -u2 'Cannot infer GitHub slug; pass --repo-slug'; exit 1; }
  fetch_base "$worktree" "$base_ref"
  load_pr_evidence "$repo_slug"
  proof_row=$(prove_merge "$worktree" "$branch" "$head" "$base_ref")
  IFS=$'\t' read -r proof pr_number pr_url <<< "$proof_row"

  mkdir -p "$plan:h"
  touch "$plan"
  print -r -- $'candidate_id\tsize_kib\tkind\tmerge_proof\tpr_number\tpr_url\tbranch\thead\tbase_ref\trepo_slug\tworktree_path\tdependency_path' >> "$plan"
  index=0
  while IFS= read -r -d '' target; do
    [[ ! -L "$target" ]] || continue
    has_unsafe_path_chars "$target" && { print -u2 "Unsafe dependency path: $target"; exit 1; }
    kind=$target:t
    [[ "$kind" == .venv || "$kind" == node_modules ]] || continue
    measured=$(du -sk "$target")
    size_kib=${measured%%$'\t'*}
    (( index += 1 ))
    candidate_id=$(printf 'D%03d' "$index")
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$candidate_id" "$size_kib" "$kind" "$proof" "$pr_number" "$pr_url" "$branch" "$head" \
      "$base_ref" "$repo_slug" "$worktree" "$target" >> "$plan"
  done < <(find "$worktree" -xdev \( -name .venv -o -name node_modules \) -type d -prune -print0)
  (( index > 0 )) || { print -u2 "No dependency directories found: $worktree"; exit 1; }
  chmod 600 "$plan"
  digest=$(shasum -a 256 "$plan")
  digest=${digest%% *}
  printf 'PLAN\t%s\nSHA256\t%s\nCANDIDATES\t%s\n' "$plan" "$digest" "$index"
}

typeset -a ids sizes kinds proofs pr_numbers pr_urls branches heads base_refs repo_slugs worktrees targets
typeset -ga apply_moved_targets apply_moved_staged
load_plan() {
  [[ -r "$plan" ]] || { print -u2 "Missing plan: $plan"; return 1; }
  awk -F '\t' 'NF != 12 { exit 1 }' "$plan" || { print -u2 'Malformed plan field count'; return 1; }
  line_number=0
  typeset -A seen_targets
  while IFS=$'\t' read -r id size kind proof pr_number pr_url branch head base repo_slug root target; do
    (( line_number += 1 ))
    if (( line_number == 1 )); then
      [[ "$id" == candidate_id && "$target" == dependency_path ]] || { print -u2 'Unexpected plan header'; return 1; }
      continue
    fi
    expected_id=$(printf 'D%03d' "$(( line_number - 1 ))")
    [[ "$id" == "$expected_id" && "$size" == <-> ]] || { print -u2 "Invalid plan row: $line_number"; return 1; }
    [[ -z ${seen_targets[$target]-} ]] || { print -u2 "Duplicate target: $target"; return 1; }
    seen_targets[$target]=yes
    ids+=("$id"); sizes+=("$size"); kinds+=("$kind"); proofs+=("$proof")
    pr_numbers+=("$pr_number"); pr_urls+=("$pr_url"); branches+=("$branch"); heads+=("$head")
    base_refs+=("$base"); repo_slugs+=("$repo_slug"); worktrees+=("$root"); targets+=("$target")
  done < "$plan"
  (( ${#targets} > 0 )) || { print -u2 'Empty plan'; return 1; }
}

preflight() {
  load_plan
  root=$worktrees[1]
  branch=$branches[1]
  head=$heads[1]
  base_ref=$base_refs[1]
  repo_slug=$repo_slugs[1]
  verify_registered_nonprimary "$root"
  [[ -z "$(git -C "$root" status --porcelain=v1)" ]] || { print -u2 "Dirty worktree: $root"; return 1; }
  [[ "$(git -C "$root" symbolic-ref --quiet --short HEAD)" == "$branch" ]] || { print -u2 'Branch changed'; return 1; }
  [[ "$(git -C "$root" rev-parse HEAD)" == "$head" ]] || { print -u2 'HEAD changed'; return 1; }
  load_live_cwds
  has_live_cwd "$root" && { print -u2 "Live process CWD in worktree: $root"; return 1; }
  fetch_base "$root" "$base_ref"
  load_pr_evidence "$repo_slug"
  proof_row=$(prove_merge "$root" "$branch" "$head" "$base_ref")
  IFS=$'\t' read -r current_proof current_pr current_url <<< "$proof_row"
  total_kib=0
  for index in {1..${#targets}}; do
    [[ "$worktrees[$index]" == "$root" && "$branches[$index]" == "$branch" && "$heads[$index]" == "$head" \
      && "$base_refs[$index]" == "$base_ref" && "$repo_slugs[$index]" == "$repo_slug" ]] || { print -u2 'Mixed-worktree plan'; return 1; }
    target=$targets[$index]
    kind=$kinds[$index]
    [[ -d "$target" && ! -L "$target" && "$target" == "$root"/* && "$target:t" == "$kind" ]] || { print -u2 "Unsafe or missing target: $target"; return 1; }
    measured=$(du -sk "$target")
    measured_kib=${measured%%$'\t'*}
    [[ "$measured_kib" == "$sizes[$index]" ]] || { print -u2 "Size changed: $target"; return 1; }
    [[ "$proofs[$index]" == "$current_proof" ]] || { print -u2 'Merge proof changed'; return 1; }
    (( total_kib += measured_kib ))
  done
  printf 'APPROVED\t%s items\t%s KiB\t%s\n' "${#targets}" "$total_kib" "$root"
}

apply_plan() {
  lock="$plan.apply.lock"
  mkdir "$lock" 2>/dev/null || { print -u2 "Plan is already locked: $lock"; exit 1; }
  success=no
  apply_exit_code=1
  apply_moved_targets=()
  apply_moved_staged=()
  finish_apply() {
    trap - EXIT
    if [[ "$success" != yes ]]; then
      for (( rollback_index=${#apply_moved_targets}; rollback_index >= 1; rollback_index-- )); do
        original=$apply_moved_targets[$rollback_index]
        staged=$apply_moved_staged[$rollback_index]
        [[ -e "$staged" && ! -e "$original" ]] && mv "$staged" "$original"
      done
    fi
    suffix=$([[ "$success" == yes ]] && print completed || print failed)
    mv "$lock" "$plan.apply.$suffix-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    exit "$apply_exit_code"
  }
  trap finish_apply EXIT
  digest=$(shasum -a 256 "$plan")
  digest=${digest%% *}
  [[ "$approved_sha" == "$digest" ]] || { print -u2 "Approval digest mismatch: current $digest"; exit 1; }
  preflight
  trash_root=${SWEEP_TRASH_ROOT:-$HOME/.Trash}
  [[ -d "$trash_root" ]] || { print -u2 "Missing Trash root: $trash_root"; exit 1; }
  bundle="$trash_root/storage-reclaim-$(date -u +%Y%m%dT%H%M%SZ)-$$-worktree-dependencies"
  mkdir -p "$bundle/items"
  manifest="$bundle/manifest.tsv"
  restore="$bundle/restore.sh"
  journal="$bundle/journal.tsv"
  touch "$manifest" "$restore" "$journal"
  print -r -- $'candidate_id\tsize_kib\toriginal_path\tstaged_path' >> "$manifest"
  print -r -- $'state\tcandidate_id\toriginal_path\tstaged_path' >> "$journal"
  print '#!/bin/zsh' >> "$restore"
  print 'set -eu' >> "$restore"
  print 'readonly MODE=${1:---dry-run}' >> "$restore"
  print '[[ "$MODE" == --dry-run || "$MODE" == --apply ]] || { print -u2 "Usage: restore.sh --dry-run|--apply"; exit 64; }' >> "$restore"
  for index in {1..${#targets}}; do
    staged="$bundle/items/$ids[$index]-$kinds[$index]"
    printf '%s\t%s\t%s\t%s\n' "$ids[$index]" "$sizes[$index]" "$targets[$index]" "$staged" >> "$manifest"
    quoted_target=${(q)targets[$index]}
    quoted_staged=${(q)staged}
    print "[[ -e $quoted_staged ]] || { print -u2 'Missing staged item: $staged'; exit 1; }" >> "$restore"
    print "[[ ! -e $quoted_target ]] || { print -u2 'Restore target exists: $targets[$index]'; exit 1; }" >> "$restore"
    print "print 'RESTORE\\t$staged\\t$targets[$index]'" >> "$restore"
    print "[[ \"\$MODE\" == --dry-run ]] || { mkdir -p ${(q)targets[$index]:h}; mv $quoted_staged $quoted_target; }" >> "$restore"
  done
  chmod 700 "$restore"
  load_live_cwds
  has_live_cwd "$worktrees[1]" && { print -u2 'Live process appeared before staging'; exit 1; }
  for index in {1..${#targets}}; do
    target=$targets[$index]
    staged="$bundle/items/$ids[$index]-$kinds[$index]"
    [[ -d "$target" && -z "$(git -C "$worktrees[1]" status --porcelain=v1)" ]] || { print -u2 "Target changed before move: $target"; exit 1; }
    measured=$(du -sk "$target")
    [[ "${measured%%$'\t'*}" == "$sizes[$index]" ]] || { print -u2 "Size changed before move: $target"; exit 1; }
    mv "$target" "$staged"
    apply_moved_targets+=("$target")
    apply_moved_staged+=("$staged")
    printf 'MOVED\t%s\t%s\t%s\n' "$ids[$index]" "$target" "$staged" >> "$journal"
    if [[ -n ${SWEEP_FAIL_AFTER_MOVES:-} && ${#apply_moved_targets} -ge $SWEEP_FAIL_AFTER_MOVES ]]; then
      print -u2 'Injected move failure'
      exit 1
    fi
  done
  success=yes
  apply_exit_code=0
  printf 'STAGED_BUNDLE\t%s\nRESTORE_DRY_RUN\t%s --dry-run\n' "$bundle" "$restore"
}

case "$command_name" in
  inventory) inventory ;;
  dry-run)
    [[ -n "$plan" && -z "$worktree" && -z "$approved_sha" ]] || { usage; exit 64; }
    preflight
    ;;
  apply)
    [[ -n "$plan" && -n "$approved_sha" && -z "$worktree" ]] || { usage; exit 64; }
    apply_plan
    ;;
  *) usage; exit 64 ;;
esac
