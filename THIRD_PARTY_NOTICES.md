# Third-party notices

No third-party source code or binaries are vendored. Runtime Python code uses the standard library, including SQLite, and invokes system-provided `du`, `ps`, `lsof`, Git and platform tools. The macOS notification helper is original Swift code compiled locally against Apple's installed frameworks. System executables, frameworks and SQLite are supplied by their respective installations, not redistributed as separate components in this package. SQLite's upstream code is [public domain](https://www.sqlite.org/copyright.html).

Development dependencies are declared in `pyproject.toml` and pinned in `uv.lock`. The build backend has a bounded version range in `pyproject.toml`; it is not locked by `uv.lock`. These dependencies are not vendored. GitHub Actions are pinned to upstream commits in the workflow.

[ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md) records design inspiration, not code provenance. Before copying or distributing another component, add its exact source URL/revision, affected files, license/copyright notice, applicable NOTICE content and our modifications here. Acknowledgement does not replace those obligations.
