#!/usr/bin/env python3
"""Read-only Git checkout discovery and recursive registration census."""
import argparse
import json
import os
from pathlib import Path
import subprocess

GENERATED = {'.git', '.venv', 'venv', 'node_modules', '.npm', '.cache', '.rustup',
             '.Trash', '.Spotlight-V100', '.fseventsd', '__pycache__'}


def git(path, *args):
    process = subprocess.run(['git', '--no-optional-locks', '-C', str(path), *args],
                             capture_output=True, text=True, timeout=30)
    if process.returncode:
        raise RuntimeError(process.stderr.strip()[:250] or 'Git lookup failed')
    return process.stdout.strip()


def discover(roots, excluded):
    markers, skips, errors = set(), [], []
    for scan_root in roots:
        def failed(error):
            errors.append({'path': error.filename, 'error': str(error)})
        for current, dirs, files in os.walk(scan_root, followlinks=False, onerror=failed):
            path = Path(current)
            if '.git' in dirs or '.git' in files:
                markers.add(str(path))
            for name in list(dirs):
                child = path / name
                reason = 'generated or opaque storage' if name in GENERATED or name.endswith('.sparsebundle') else None
                if any(child == p or p in child.parents for p in excluded):
                    reason = 'explicit protected or coverage exclusion'
                if child.is_symlink():
                    reason = 'symlink; not traversed'
                if reason:
                    dirs.remove(name)
                    skips.append({'path': str(child), 'reason': reason})
    pending, checked, common_dirs, registrations = sorted(markers), set(), {}, {}
    while pending:
        path = pending.pop(0)
        if path in checked:
            continue
        checked.add(path)
        if not Path(path).is_dir():
            continue
        try:
            common = git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir')
            if common in common_dirs:
                continue
            listing = git(path, 'worktree', 'list', '--porcelain')
            common_dirs[common] = path
            for block in listing.split('\n\n'):
                row = {}
                for line in block.splitlines():
                    key, _, value = line.partition(' ')
                    row[key] = value
                target = row.get('worktree')
                if not target:
                    continue
                row.update(common_dir=common, exists=Path(target).exists(),
                           discovered_by_marker=target in markers)
                registrations[target] = row
                if target not in checked:
                    pending.append(target)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            errors.append({'path': path, 'error': str(error)})
    return {'scan_roots': [str(p) for p in roots],
            'explicit_exclusions': [str(p) for p in excluded],
            'markers': sorted(markers), 'common_directories': common_dirs,
            'worktrees': sorted(registrations.values(), key=lambda r: r['worktree']),
            'unresolved_markers': sorted(markers - set(registrations)),
            'skipped': skips, 'errors': errors,
            'coverage': 'Filesystem roots and recursive Git registrations; exclusions are not audited as empty'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, action='append', help='Default: entire home directory')
    parser.add_argument('--exclude', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    protected = [Path.home() / 'Library/Application Support',
                 Path.home() / 'Library/Mobile Documents',
                 Path.home() / 'Library/CloudStorage']
    result = discover(args.root or [Path.home()], protected + args.exclude)
    with args.output.open('x') as stream:
        stream.write(json.dumps(result, indent=2) + '\n')
    print('Markers:', len(result['markers']), 'common repositories:', len(result['common_directories']),
          'registered worktrees:', len(result['worktrees']), 'coverage errors:', len(result['errors']))
    print('Coverage ledger:', args.output)


if __name__ == '__main__':
    main()
