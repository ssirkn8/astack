# Data Contract

Keep raw inputs immutable, declare every source in `data/sources.yml`, and isolate derived outputs by component and worktree.

## Folder Roles

- `sources.yml` is the machine-readable authority for provenance, licensing, sensitivity, and release rules.
- `raw/` holds immutable source snapshots. Never edit or delete files in place. It is the only data folder shared across linked Git worktrees.
- `manual/` holds human-curated inputs or annotations (worktree-local). Keep original snapshots in `raw/` and record the curation step in `sources.yml`.
- `interim/<component>/` holds reproducible intermediate datasets for downstream steps (mutable, worktree-local, not a dumping ground).
- `processed/<component>/` holds analysis-ready products (mutable, reproducible when practical, worktree-local).

Keep other outputs scoped by component too: `artifacts/<component>/` for machine outputs (models, predictions), `evidence/<component>/` for durable QA and promoted run records, `reports/figures/<component>/` for figures, `reports/tables/<component>/` for tables, and `scratch/` for disposable local work (never evidence). Register components under `[outputs]` in `.astack/project.toml`.

## Worktrees and Immutability

Before creating linked worktrees, populate and verify `data/raw/`, then lock it read-only:

```sh
chmod -R a-w data/raw
```

In each linked worktree, run `astack run share-raw` to preview and `astack run share-raw --apply` to link `data/raw/`. Worktree sharing refuses writable inputs. `manual/`, `interim/`, `processed/`, reports, and `.venv` stay local to each worktree.

DVC runs the pipeline and tracks stale outputs, while cache storage stays machine-local and shared per repository across worktrees. astack never runs `dvc pull` or `dvc push` automatically.

## Source Provenance (`data/sources.yml`)

Each schema v2 record documents title, path or URL, origin, provider, access date, version, identity, license, sensitivity, redistribution rights, `allowed_outputs` (such as `internal-analysis`, `aggregate-tables`, `derived-figures`, or `public-export`), and citation.

License, sensitivity, redistribution, citation, and identity each take one of four states:

- `known` (requires a value)
- `not_applicable` (requires a reason)
- `unknown`
- `not_yet_verified`

Recording `unknown` or `not_yet_verified` is honest and structurally valid during exploration, but it always blocks release checks.

### Checksums and External Verification

Compute the exact `raw_checksum` value to record (`sha256:<hex>` for a file, `sha256-tree-v2:<hex>` for a directory):

```sh
astack run checksum data/raw/<file-or-dir>
```

External URL sources also need a versioned acquisition or retrieval procedure and a verification record binding the retrieved bytes to the source entry:

```sh
astack run verify-source <source-id> <retrieved-file-or-directory>
```

Commit the generated `data/source-verification/<source-id>.json` and point `verification:` in `sources.yml` at it. The record proves which local bytes were hashed and checked against any declared digest, not that the remote server will serve the same bytes later, so prefer immutable releases or object-store generations.

Routine audits cache digests for unchanged read-only files (`ASTACK_DIGEST_CACHE=off` disables caching). Strict audits (`just ship-ready` and `just release`) always rehash files directly.

### Limited CI for Local Raw Sources

Because `/data/raw` is gitignored, new Data Science projects default to limited CI: `[ci] mode = "local-only-sources"` in `.astack/project.toml` and `just ci-check` in `.github/workflows/checks.yml`.

- In this mode, gitignored `data/raw/` snapshots and `storage: in_place` sources are treated as `local_only` by default. Missing files in CI appear as visible P2 `not-verified-in-ci` findings instead of failing the PR, while any file present in CI is still checksum-verified.
- Set `availability: ci_available` on a source if CI must fail when it is absent (for example, data fetched in CI or committed under `data/manual/`). You may also declare `availability: local_only` explicitly on any local-only source.
- Limited CI never marks `computational_reproducibility` or `release_readiness` as `pass`. Run strict local `just ship-ready` on a machine that has the data before shipping.

## Acquisition vs. Reproduction

- **Acquisition (`just acquire`):** runs `[acquisition] command` from `.astack/project.toml` inside an isolated `$ASTACK_ACQUISITION_DIR`. Requires `data/raw/` to be read-only first, moves newly staged files in as read-only without overwriting existing paths, and leaves original snapshots untouched on failure.
- **Reproduction (`just reproduce`):** consumes retained local inputs only and never downloads mutable upstream data.

## External Sources Kept in Place (`storage: in_place`)

Snapshot sources into `data/raw/` by default. Any local source kept outside `data/raw/` (such as a curated file in `data/manual/`) must be committed in this repository, never left in an ignored directory, behind or containing a symlink, or inside or containing a nested repository or submodule. Keep a source outside the repository only for one of two recorded reasons:

- `in_place_reason: size` when total size exceeds `[data] snapshot_limit_gb` in `.astack/project.toml` (default `5` GB per source).
- `in_place_reason: license` when terms forbid copying the data locally.

Reference it with `storage: in_place` under a named external root (lowercase letters, digits, and underscores):

```yaml
survey:
  path: root:archive/survey/2026
  storage: in_place
  in_place_reason: size
  raw_checksum: sha256-tree-v2:<digest>
```

Map the root on each machine in gitignored `.astack/local.toml` or via `ASTACK_EXTERNAL_ROOT_ARCHIVE`:

```toml
[data.external_roots]
archive = "/Volumes/Shared/archive"
```

The audit hashes the source where it lives (strict audits always rehash), `just reproduce` passes `ASTACK_EXTERNAL_ROOT_<NAME>` to your pipeline, and astack never writes to external roots. Keep the external copy unchanged.

## Avoid Retaining Finished Stage Worktrees

Plan durable ownership before running a stage. Before downstream work depends on a temporary worktree, freeze required products into a durable project or external location, record their provenance and checksum in `sources.yml`, and verify reproduction with the old worktree absent. See [Data ownership](../docs/data-ownership.md).
