# Data Contract

- `sources.yml` is the machine-readable authority for provenance and release constraints.
- `raw/` contains immutable source snapshots. It is the only data directory shared between linked
  worktrees, and it must never be edited in place.
- `manual/` contains human-curated inputs or annotations. Keep the original source in `raw/` and
  record the curation step and resulting provenance before using a manual input.
- `interim/<component>/` contains reproducible intermediate datasets consumed by downstream steps.
  It is mutable and worktree-local, but it is not a general dumping ground.
- `processed/<component>/` contains analysis-ready data products. It is mutable, reproducible when
  practical, and worktree-local.
- External sources require a versioned acquisition procedure and verifiable identity such as a
  SHA-256 digest, immutable release, DVC hash, or object-store generation. A declared identity is
  only a claim. It stays unverified, and blocks release, until `verification` points to a record
  that binds it to a digest of the bytes actually retrieved (see below).
- DVC runs the pipeline and labels stale outputs. Use it to store data only when data size,
  volatility, or collaboration needs justify it.
- DVC cache configuration is machine-local. Canonical and linked worktrees use the same
  per-repository cache, while worktree directories remain isolated. The workflow never runs
  `dvc pull` or `dvc push` automatically.

Schema v2 source records describe title, path, origin, provider, access date, version, source
identity, license, sensitivity, redistribution rights, allowed derived outputs, and citation.
License, sensitivity, redistribution, citation, and identity use one of `known`, `unknown`,
`not_applicable`, or `not_yet_verified`. `known` requires a value and `not_applicable` requires a
reason. Unknown and unverified evidence is honest and structurally valid, but never release-ready.
Use `allowed_outputs` as a non-empty list such as `internal-analysis`, `aggregate-tables`, or
`derived-figures`. External sources also require a versioned acquisition or retrieval procedure.

### Verifying an external source

After retrieving an external source, hash what you retrieved:

```bash
uv run --locked python .astack/workflow.py verify-source <source-id> <retrieved-file-or-directory>
```

This writes `data/source-verification/<source-id>.json` with the source id, URL, declared identity,
SHA-256 digest of the retrieved bytes, and time. Commit it and set
`verification: data/source-verification/<source-id>.json` on the source record. The audit checks
that the record matches the declared path, identity, and any `raw_checksum`, and that a `sha256`
identity equals the digest. Any hosting service works. The record proves which bytes were retrieved
and hashed. It does not prove the upstream location will serve the same bytes again, so prefer
immutable versions or object generations.

Use `sha256:<digest>` for one file and `sha256-tree-v2:<digest>` for a directory. Run
`python .astack/workflow.py checksum <path>` to print the value to record. The tree digest starts
from the domain prefix `astack-sha256-tree-v2` and a null byte, then hashes, for each regular file
in sorted relative-path order, the path, a null byte, the file's 64-character hex SHA-256, and a
newline. A symlink to a directory, an unreadable folder, or any other non-regular entry inside the
tree fails the check instead of being skipped.

The older `sha256-tree-v1:<digest>` is still compared, so a changed directory is still detected,
but it is reported as a P1 finding that blocks release: its framing (path, null byte, raw file
bytes, null byte) lets two different directories share a digest when file contents contain null
bytes. Replace it with the `checksum` command's value.

The standard worktree topology shares only `raw/`. `manual/`, `interim/`, and `processed/` are
always worktree-local. Before creating a linked worktree, populate and verify the raw snapshot, then
run `chmod -R a-w data/raw` or apply equivalent read-only permissions to the configured external data
root. Worktree creation refuses writable shared inputs. Existing nonstandard projects may use an
explicit legacy layout during migration.

## Acquisition and reproduction

Acquisition is the explicit operation that obtains or adds retained inputs. Reproduction consumes
those retained inputs and should not download mutable upstream data. Record acquisition procedures
and source identity in `sources.yml`, then keep existing snapshots immutable.

Large raw data is not copied or rehashed needlessly. Audits, reproduction, and acquisition reuse a
file's SHA-256 only for a read-only file whose device, inode, size, modification time,
status-change time, and permissions are unchanged. Programs cannot set the status-change time, so
any write or permission change forces a rehash. Writable files are always rehashed, so make raw
snapshots read-only (`chmod -R a-w data/raw`) to benefit. Strict audits (`just ship-ready` and release) always
rehash, because a process that mapped a file writable before it was made read-only could still
change it without changing its timestamps. The cache is
machine-local, kept in the Git common directory outside the worktree, and
`ASTACK_DIGEST_CACHE=off` disables it.

Acquisition is staged. The command gets an empty `$ASTACK_ACQUISITION_DIR`, on the same drive as
`data/raw` where possible, and never writes to `data/raw` itself. astack keeps no copy of
`data/raw`, so it refuses to start unless raw is already read-only (`chmod -R a-w data/raw`),
fully readable, and free of symlinks other than a shared `data/raw` link itself. Anything the
command leaves running is stopped when it exits. On success astack adds the staged files to
`data/raw`, read-only, and refuses any path that already exists and any staged hard link. On
failure the staging directory is deleted, and astack removes only paths it can prove it created.
Cleanup never deletes an original raw file or directory, wherever it finds one, and reports
where it kept it. Known limit: a process that outlives the acquisition command, or a command that
deliberately makes raw writable again, can still interfere, and nothing restores it.

### Sources that stay where they are

Snapshot sources into `raw/` through acquisition by default. A local source elsewhere in the
project must be committed to Git, such as a curated file in `manual/`, and a source in an ignored
folder such as `interim/`, behind or containing a symlink, or inside a nested repository or
submodule, is flagged. A source may stay outside the project
only for one of two recorded reasons:

- `in_place_reason: size`, when its total size is above `[data] snapshot_limit_gb` in
  `.astack/project.toml` (default 5 GB, per source). A smaller source is flagged until it is
  snapshotted.
- `in_place_reason: license`, when its terms forbid copying it.

Record it with `storage: in_place` and a path under a named external root:

```yaml
survey:
  path: root:archive/survey/2026
  storage: in_place
  in_place_reason: size
  raw_checksum: sha256-tree-v2:<digest>
```

Root names use lowercase letters, digits, and underscores. Each machine says where `archive`
lives, outside the project, in `.astack/local.toml` (not
committed) or in the `ASTACK_EXTERNAL_ROOT_ARCHIVE` environment variable:

```toml
[data.external_roots]
archive = "/Volumes/Shared/archive"
```

The SHA-256 `raw_checksum` is required, and a directory needs `sha256-tree-v2`. The audit
rehashes the source where it lives, under the same digest-cache rule as `raw/` (strict audits
always rehash), and
reproduction refuses to run when it is unavailable or does not match. The reproduction command
receives `ASTACK_EXTERNAL_ROOT_ARCHIVE`, so code reads the source from there. astack never writes
to an external root, so a shared drive, or one formatted as exFAT, is a read-only reference. Keep
it unchanged, because nothing in the project holds a copy.

## Output lifecycle

Outputs are scoped by component so that one analysis cannot silently overwrite another:

- `artifacts/<component>/` holds generated machine outputs such as serialized models or predictions.
- `evidence/<component>/` holds durable machine-readable QA, validation, and run evidence.
- `reports/figures/<component>/` holds figures referenced by reports.
- `reports/tables/<component>/` holds tables referenced by reports.
- `scratch/` holds disposable local work and is never evidence.

Declare component names in the `[outputs]` section of `.astack/project.toml` as the project grows.

## Avoid retaining finished stage worktrees

Use durable, worktree-independent input ownership from the beginning. Before a downstream stage
or publication depends on a temporary producer, freeze required products into an existing durable
project/external location, record expected identity and derivation, and verify reproduction with
old-stage reads denied. Keep mutable interim/processed directories worktree-local. Preserve manual
or sole-copy data even when ignored. A verified retained-product render is not a from-raw rebuild.
See [the concrete ownership and retention guide](../docs/data-ownership.md). No data is relocated
or cleanup protection removed automatically by these instructions.
