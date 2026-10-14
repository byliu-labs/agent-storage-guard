# Storage measurements

The guard uses the installed `/usr/bin/du` with `-skxP`: one root summary,
KiB accounting blocks, no filesystem crossing, and no symlink following.
It does not install scanners or inspect file contents. Sparse holes are excluded
and hardlinks count once. On APFS, clone accounting can include shared blocks;
the result is not exclusively owned physical storage. `free_bytes(path)` reads
the native volume free-space signal separately using `shutil.disk_usage`.

`allocated(root, timeout=2.0)` accepts a finite timeout in `(0, 2]` seconds.
The total scan deadline covers both attempts; only diagnostics consisting
entirely of missing-file races permit one retry. Permission gaps, malformed
totals, warnings, deadline expiry, and changed root identity raise
`MeasurementError`. Partial measurements never become zero or a trusted total.
The scanner's captured output is capped at 64 KiB and diagnostic text is not
copied into error messages.

An owned scanner process group receives termination and then a kill signal on
failure. The parent is reaped. Cleanup has a separate bounded allowance of up
to 0.65 seconds. If the kernel does not release a killed process within that
allowance, measurement fails visibly rather than waiting indefinitely.

Roots and ancestors must exist without symlinks or parent traversal. The root's
device and inode are checked before and after scanning. These checks serve a
cooperative command wrapper, not a security boundary against hostile programs
running as the same user. The guard owns directory permissions and admission.

Tests use actual native scans for sparse files, hardlinks, symlinks, unreadable
directories, missing roots, and free space. Local executable helpers exercise
bounded retry, timeout and child teardown, output overflow, warnings, malformed
totals, and root replacement. Helpers are injected through a private test seam;
users cannot configure an arbitrary measurement executable through this API.
