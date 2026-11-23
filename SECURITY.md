# Security policy

## Reporting a vulnerability

Use GitHub private vulnerability reporting: **Security → Advisories → Report a vulnerability**
on this repository. Report suspected token exposure, unintended code execution, unsafe file
writes or other security problems privately. Include the affected version or commit,
reproduction steps and the expected impact. Redact credentials and any personal or restricted
research data.

Do not put exploit details, working credentials or sensitive data in public issues. If the
private report button is unavailable, open a nonsensitive issue asking for a private contact
route, without the vulnerability details. Ordinary bugs can use issues.

## Supported versions

Only the latest released version receives security fixes. Response times are best effort.
Disclosure is coordinated after investigation and a verified fix. A report does not grant
permission to test against other people's projects, production systems or private data.

## Upstream DVC cache boundary

Data science projects install DVC by default. DVC's `dvc-data` dependency uses `diskcache`,
which has an unpatched upstream advisory (`CVE-2025-69872` / `GHSA-w8v5-vhqr-4h9v`): an attacker
who can write to a `diskcache` directory can achieve arbitrary code execution when DVC reads its
pickled cache entries. DVC stores that `diskcache` state under `Repo.site_cache_dir`, whose
upstream default is a shared cache root (`/Library/Caches/dvc` on macOS or `/var/tmp/dvc` on
Linux) with a `0777` `repo/` subdirectory.

When astack configures DVC, it creates `.dvc/tmp/site-cache` and the per-repository object cache
(`~/Library/Caches/astack/dvc/` on macOS, `~/.cache/astack/dvc/` on Linux) with mode `0700` on
POSIX, writes `[core] site_cache_dir` and `[cache] dir` into gitignored `.dvc/config.local`,
isolates uninitialized read-only probes in a `0700` temporary `DVC_SITE_CACHE_DIR`, and refuses
symlinked, group/other-writable, or writable-parent cache paths. Inside `site_cache_dir`, DVC
temporarily sets the process umask to `0` when creating `repo/` (mode `0777`) and then restores
the process umask for deeper subdirectories (such as `repo/<hash>/`), so isolation relies on the
enclosing `0700` directory. Read-only probes set a `0077` umask before opening DVC, so any newly
created `.dvc/tmp` directory is also `0700` on POSIX.

If you invoke `dvc` directly before astack initializes `.dvc/config.local`, or override
`DVC_SITE_CACHE_DIR`, `[core].site_cache_dir`, `ASTACK_DVC_CACHE_ROOT`, or `[dvc].cache_dir`,
keep those directories private to your user account (`0700` on POSIX). Do not point them at a
shared or multi-user writable directory, and never reuse a DVC site cache or `.dvc/tmp/`
directory from an untrusted source.
