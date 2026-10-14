# Third-party notices

No third-party source code or binaries are vendored in this initial extraction. Runtime Python code uses the standard library and invokes the system-provided `du` command. The operating system supplies that executable under its own license; it is not redistributed in this package.

Development/build dependencies are declared in `pyproject.toml` and pinned in `uv.lock`. They are not vendored. GitHub Actions are pinned to upstream commits in the workflow.

[ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md) records design inspiration, not code provenance. Before copying or distributing another component, add its exact source URL/revision, affected files, license/copyright notice, applicable NOTICE content and our modifications here. Acknowledgement does not replace those obligations.
