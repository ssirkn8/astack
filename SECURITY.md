# Security policy

## Reporting a vulnerability

Report suspected token exposure, unintended code execution, unsafe file writes, or other security issues privately through GitHub: **Security -> Advisories -> Report a vulnerability** on `ssirkn8/astack`.

Include the affected version or commit, reproduction steps, and the expected impact. Redact all credentials and any personal or restricted research data.

Do not post exploit details, working credentials, or sensitive data in public issues. If GitHub private vulnerability reporting is unavailable, open a minimal issue asking for a private contact route without including vulnerability details. Ordinary bugs belong in normal issues.

## Supported versions

Only the latest published release receives security fixes. Response times are best effort, and disclosure is coordinated after investigation and a verified fix. Filing a report does not grant permission to test against other people's projects, production systems, or private data.

## Upstream DVC cache boundary

Data science projects install DVC by default. DVC's `dvc-data` dependency uses `diskcache`, which has an unpatched upstream advisory (`CVE-2025-69872` / `GHSA-w8v5-vhqr-4h9v`): anyone who can write to a `diskcache` directory can achieve arbitrary code execution when DVC reads its pickled cache entries. By default, DVC stores that state under `Repo.site_cache_dir`, which upstream places in a shared cache root (`/Library/Caches/dvc` on macOS or `/var/tmp/dvc` on Linux) with a `0777` `repo/` subdirectory.

When astack configures DVC, it enforces a user-private boundary:

- Creates `.dvc/tmp/site-cache` and the per-repository object cache (`~/Library/Caches/astack/dvc/` on macOS, `~/.cache/astack/dvc/` on Linux) with mode `0700` on POSIX.
- Writes `[core] site_cache_dir` and `[cache] dir` into gitignored `.dvc/config.local`.
- Isolates uninitialized read-only probes in a `0700` temporary `DVC_SITE_CACHE_DIR` and sets a `0077` umask before opening DVC so any newly created `.dvc/tmp` directory is also `0700` on POSIX.
- Refuses symlinked, group-writable, world-writable, or writable-parent cache paths.

Inside `site_cache_dir`, DVC temporarily sets the process umask to `0` while creating `repo/` (mode `0777`) and then restores the umask for deeper subdirectories such as `repo/<hash>/`. Isolation therefore depends on the enclosing `0700` directory.

If you run `dvc` directly before astack initializes `.dvc/config.local`, or if you override `DVC_SITE_CACHE_DIR`, `[core].site_cache_dir`, `ASTACK_DVC_CACHE_ROOT`, or `[dvc].cache_dir`, keep every target directory private to your user account (`0700` on POSIX). Never point those settings at a shared or multi-user writable directory, and never reuse a DVC site cache or `.dvc/tmp/` directory from an untrusted source.
