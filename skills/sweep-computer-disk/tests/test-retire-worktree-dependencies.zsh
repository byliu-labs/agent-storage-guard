#!/bin/zsh
set -eu

SCRIPT=${0:A:h:h}/scripts/retire-worktree-dependencies.zsh
fixture=$(mktemp -d /private/tmp/retire-worktree-deps.XXXXXX)
mkdir -p "$fixture/bin" "$fixture/trash"

print '#!/bin/zsh' > "$fixture/bin/gh"
print '[[ "$GH_PR_ROWS" == "-" ]] || print -r -- "$GH_PR_ROWS"' >> "$fixture/bin/gh"
print '#!/bin/zsh' > "$fixture/bin/lsof"
print '[[ -z ${TEST_LSOF_ROWS:-} ]] || print -r -- "$TEST_LSOF_ROWS"' >> "$fixture/bin/lsof"
chmod 700 "$fixture/bin/gh" "$fixture/bin/lsof"
export PATH="$fixture/bin:$PATH"
export GH_PR_ROWS='-'

git init -q --bare "$fixture/origin.git"
git clone -q "$fixture/origin.git" "$fixture/repo"
git -C "$fixture/repo" config user.email test@example.com
git -C "$fixture/repo" config user.name Test
print '.venv/' > "$fixture/repo/.gitignore"
print 'node_modules/' >> "$fixture/repo/.gitignore"
print 'root' > "$fixture/repo/README.md"
git -C "$fixture/repo" add .
git -C "$fixture/repo" commit -qm initial
git -C "$fixture/repo" branch -M main
git -C "$fixture/repo" push -qu origin main
git -C "$fixture/origin.git" symbolic-ref HEAD refs/heads/main
git -C "$fixture/repo" remote set-url origin git@github.com:test/example.git
git -C "$fixture/repo" remote add local "$fixture/origin.git"
git -C "$fixture/repo" config remote.origin.url "$fixture/origin.git"
git -C "$fixture/repo" config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
git -C "$fixture/repo" fetch -q origin
git -C "$fixture/repo" remote set-head origin main
git -C "$fixture/repo" worktree add -qb feature/merged "$fixture/worktree"
print 'feature' > "$fixture/worktree/feature.txt"
git -C "$fixture/worktree" add feature.txt
git -C "$fixture/worktree" commit -qm feature
git -C "$fixture/repo" merge -q --ff-only feature/merged
git -C "$fixture/repo" push -qu origin main
mkdir -p "$fixture/worktree/.venv/bin" "$fixture/worktree/web/node_modules/pkg"
print python > "$fixture/worktree/.venv/bin/python"
print module > "$fixture/worktree/web/node_modules/pkg/index.js"

plan="$fixture/plan.tsv"
"$SCRIPT" inventory --worktree "$fixture/worktree" --plan "$plan" --base origin/main --repo-slug test/example
"$SCRIPT" dry-run --plan "$plan"
digest=$(shasum -a 256 "$plan")
digest=${digest%% *}

if SWEEP_TRASH_ROOT="$fixture/trash" "$SCRIPT" apply --plan "$plan" --approved-sha256 deadbeef; then
  print -u2 'wrong digest unexpectedly accepted'
  exit 1
fi
[[ -d "$fixture/worktree/.venv" && -d "$fixture/worktree/web/node_modules" ]]

if TEST_LSOF_ROWS="n$fixture/worktree" "$SCRIPT" dry-run --plan "$plan"; then
  print -u2 'live CWD unexpectedly accepted'
  exit 1
fi

SWEEP_TRASH_ROOT="$fixture/trash" SWEEP_FAIL_AFTER_MOVES=1 "$SCRIPT" apply --plan "$plan" --approved-sha256 "$digest" && {
  print -u2 'injected failure unexpectedly succeeded'
  exit 1
}
[[ -d "$fixture/worktree/.venv" && -d "$fixture/worktree/web/node_modules" ]]

mkdir "$plan.apply.lock"
if SWEEP_TRASH_ROOT="$fixture/trash" "$SCRIPT" apply --plan "$plan" --approved-sha256 "$digest"; then
  print -u2 'concurrent plan lock unexpectedly accepted'
  exit 1
fi
mv "$plan.apply.lock" "$plan.apply.lock-test-completed"

SWEEP_TRASH_ROOT="$fixture/trash" "$SCRIPT" apply --plan "$plan" --approved-sha256 "$digest"
[[ ! -e "$fixture/worktree/.venv" && ! -e "$fixture/worktree/web/node_modules" ]]
bundle=$(find "$fixture/trash" -maxdepth 1 -type d -name 'storage-reclaim-*' | sort | tail -n 1)
[[ -r "$bundle/manifest.tsv" && -x "$bundle/restore.sh" && -r "$bundle/journal.tsv" ]]
"$bundle/restore.sh" --dry-run
"$bundle/restore.sh" --apply
[[ -d "$fixture/worktree/.venv" && -d "$fixture/worktree/web/node_modules" ]]

git -C "$fixture/repo" worktree add -qb feature/unmerged "$fixture/unmerged"
print unmerged > "$fixture/unmerged/unmerged.txt"
git -C "$fixture/unmerged" add unmerged.txt
git -C "$fixture/unmerged" commit -qm unmerged
mkdir -p "$fixture/unmerged/.venv"
export GH_PR_ROWS=$'99\tMERGED\tfeature/unmerged\t0000000000000000000000000000000000000000\thttps://github.com/test/example/pull/99'
if "$SCRIPT" inventory --worktree "$fixture/unmerged" --plan "$fixture/unmerged.tsv" --base origin/main --repo-slug test/example; then
  print -u2 'merged PR with mismatched head OID unexpectedly accepted'
  exit 1
fi
export GH_PR_ROWS='-'

primary_plan="$fixture/primary.tsv"
if "$SCRIPT" inventory --worktree "$fixture/repo" --plan "$primary_plan" --base origin/main --repo-slug test/example; then
  print -u2 'primary checkout unexpectedly accepted'
  exit 1
fi

print 'PASS retire-worktree-dependencies'
