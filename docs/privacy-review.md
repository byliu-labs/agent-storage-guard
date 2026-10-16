# Publication privacy review

Reviewed on 2026-10-16 against main `94374e3` and this change.

## Files and bundled skill

Reviewed the tracked source, tests, docs, configuration, CI and dependency lockfile,
plus the complete bundled sweep skill (instructions, scripts, reference and tests).
Pattern checks covered personal home paths, personal email addresses, private
project identifiers and common credential signatures. No personal paths, personal
email addresses or credential signatures were found in the reviewed file contents.
Synthetic fixture paths and example email addresses are intentional.

Replaced a workstation-specific application exclusion with protection for the whole
private macOS Application Support directory in the monitor and discovery helper.
The bundled skill has no dependency on a private skill or workstation layout.
The original locally installed skill is unchanged.

The repository owner, copyright attribution, upstream acknowledgements and stable
notification bundle ID remain public project identifiers. Changing the bundle ID
would disrupt existing notification identity rather than anonymize user data.

## History and GitHub surface

The review also inspected all reachable local Git blobs and commit metadata after
fetching origin, repository metadata, PR/issue text and comments, and releases.
Four commits reachable from main contain a personal author identity.
Older file revisions also retain the application-specific exclusion. Editing files
in a PR cannot erase those copies. No history was rewritten by this change.

To remove historical identity, separately approve and coordinate a history rewrite,
including affected branches/tags and GitHub PR refs/cached views as applicable.
Existing clones/forks cannot be recalled. Use the repository's GitHub noreply
identity for new commits; choose a matching private commit email in GitHub before
merging because GitHub-generated merge/squash commits have their own attribution.

## Runtime data is private

Do not publish `~/.asg`, real HTML reports, SQLite history, receipts, investigation
prompts, cleanup manifests or discovery ledgers: these can identify usernames,
paths, repositories, processes and machine activity. The root `output/` directory
is ignored for private run artifacts; inspect staged files before publishing.

This is a scoped review, not a guarantee that every possible secret format or
personal inference can be detected. GitHub account/profile information and copies
outside this repository are outside the file cleanup.
