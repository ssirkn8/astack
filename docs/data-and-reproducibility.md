# Data and reproducibility

Data science projects fail silently when raw inputs mutate, derived files leak across parallel experiments, or nobody knows whether a figure came from the current commit. astack enforces clear folder ownership, machine-readable source provenance, and clean-tree pipeline reproduction.

## Folder ownership and worktrees

```text
data/
  sources.yml          machine-readable provenance for every source
  raw/                 immutable source snapshots, shared across linked worktrees
  manual/              human-curated inputs (keep originals in raw/, worktree-local)
  interim/<c>/         reproducible intermediates, per component, worktree-local
  processed/<c>/       analysis-ready products, per component, worktree-local
artifacts/<c>/         generated machine outputs (models, predictions, diagnostics)
evidence/<c>/          durable QA, validation, and promoted run records
reports/figures/<c>/   report figures
reports/tables/<c>/    report tables
scratch/               disposable local work, never counts as evidence
```

Every output directory is scoped by component name (`<c>`, declared in `[outputs] components` in `.astack/project.toml`) so separate analyses do not overwrite each other.

**Worktree isolation.** You can run multiple agents in parallel Git worktrees safely:

1. Populate and verify `data/raw/` in your canonical checkout, then lock it read-only: `chmod -R a-w data/raw`.
2. In each linked worktree, preview the shared link with `astack run share-raw` and apply it with `astack run share-raw --apply`.
3. Only `data/raw/` is shared. `data/manual/`, `data/interim/`, `data/processed/`, reports, scratch files, and virtual environments stay local to each worktree.

**Do not treat temporary worktrees as permanent storage.** If a downstream stage or publication depends on an expensive derived product, freeze that product into a durable project or external location, record its provenance and checksum in `data/sources.yml`, and verify reproduction with the old worktree absent before removing it.

## Source provenance (`data/sources.yml`)

`data/sources.yml` (schema v2) is the machine-readable record of where every input came from and what you are allowed to publish from it. Each entry records title, path or URL, origin, provider, access date, version, license, sensitivity, redistribution rights, `allowed_outputs` (for example `internal-analysis`, `aggregate-tables`, `derived-figures`, or `public-export`), citation, and identity.

**Explicit evidence states.** License, sensitivity, redistribution, citation, and identity each take one of four states:

- `known` (with a real value)
- `not_applicable` (with a reason)
- `unknown`
- `not_yet_verified`

Recording `unknown` or `not_yet_verified` is honest and structurally valid while you work, but it always blocks release checks.

**Checksums.** Compute the exact `raw_checksum` value to record with:

```sh
astack run checksum data/raw/survey.parquet
astack run checksum data/raw/survey-dir
```

Files use `sha256:<hex>` and directories use `sha256-tree-v2:<hex>` (which hashes sorted relative paths and per-file digests, rejecting symlinks and non-regular entries). Legacy `sha256-tree-v1` digests are still checked for drift, but they raise a P1 release blocker because two different directories containing null bytes can collide under v1. Replace them with `astack run checksum`.

**Verifying external URL sources.** A source identified only by URL must also declare how to fetch it (`acquisition` or `retrieval`). After downloading it, bind the retrieved bytes to the record:

```sh
astack run verify-source <source-id> /path/to/retrieved-file-or-dir
```

This writes `data/source-verification/<source-id>.json` with the source ID, declared URL, declared identity, SHA-256 of the supplied path, and timestamp. Commit that JSON file and point `verification:` in `data/sources.yml` at it. The record binds the local bytes you supplied and hashed to the declared source and checks any declared digest. Obtaining those bytes from the upstream source remains a separate step, and the record does not prove the remote server will return identical bytes in the future, so prefer immutable release URLs or object-store generations.

**Raw immutability and digest caching.** The audit rehashes local sources and flags any change under `data/raw/` as P0. For large read-only snapshots (`chmod -R a-w data/raw`), routine audits reuse a machine-local digest cache keyed on device, inode, size, modification time, status-change time, and permissions (disable with `ASTACK_DIGEST_CACHE=off`). Strict audits (`just ship-ready` and `just release`) always rehash raw bytes directly.

## Acquisition vs. reproduction

Acquisition and reproduction are deliberately separate:

- `just acquire` (`astack run acquire`) runs `[acquisition] command` from `.astack/project.toml`. It is the only step allowed to add files to `data/raw/`. Acquisition is staged: astack passes an empty `$ASTACK_ACQUISITION_DIR`, requires `data/raw/` to already be read-only, and moves newly staged files into `data/raw/` as read-only without copying existing raw trees or overwriting existing paths.
- `just reproduce` (`astack run reproduce`) consumes only inputs that already exist. It never downloads remote data and never runs `dvc pull`.

## External sources verified in place (`storage: in_place`)

Snapshot sources into `data/raw/` by default. Keep a source outside the repository only when you declare `storage: in_place` with one of two explicit reasons:

- `in_place_reason: size` when the source's total size exceeds `[data] snapshot_limit_gb` in `.astack/project.toml` (default `5` GB per source). Smaller sources are flagged until snapshotted.
- `in_place_reason: license` when data terms forbid making a local copy.

Reference the source through a named root in `data/sources.yml`:

```yaml
survey:
  path: root:archive/survey/2026
  storage: in_place
  in_place_reason: size
  raw_checksum: sha256-tree-v2:<digest>
```

Map the root name on each machine in gitignored `.astack/local.toml` or via an environment variable (`ASTACK_EXTERNAL_ROOT_ARCHIVE`):

```toml
[data.external_roots]
archive = "/Volumes/Shared/archive"
```

The audit rehashes the source where it lives (strict audits always rehash), and `just reproduce` refuses to run if the source is missing or changed. During reproduction, astack passes `ASTACK_EXTERNAL_ROOT_<NAME>` to your pipeline command so your code never hardcodes machine-specific paths. astack never writes to external roots, so shared or exFAT drives work safely as read-only references.

## Pipeline reproduction

Once results will support a talk, report, paper, or decision, set `[analysis] stage = "material"` and declare `[reproduction]` in `.astack/project.toml`:

```toml
[analysis]
stage = "material"

[reproduction]
engine = "dvc"
component = "analysis"
targets = ["prepare", "model"]
outputs = [
  "data/processed/analysis/summary.parquet",
  "reports/figures/analysis/main-result.png",
]
random_seed = 42
```

When you run `just reproduce`, astack:

1. Refuses to start if the Git worktree is dirty or `[reproduction] outputs` is empty.
2. Records the Git commit, `sources.yml`, `.astack/project.toml`, `uv.lock`, a digest of raw inputs, and the Python interpreter's installed packages and native libraries (`unavailable` for non-Python commands).
3. Exports `ASTACK_RANDOM_SEED` when `random_seed` is configured.
4. Executes the declared pipeline.
5. Fails if the run modified `data/raw/`, wrote outside allowed output folders, mutated its own inputs or environment, or omitted any declared output.
6. Hashes every declared output and writes a run record to `.cache/runs/<component>/` (and with `--promote`, writes a durable record to `evidence/<component>/runs/`).

**Proving repeatability.** A single successful run (`just reproduce --promote`) records status `command-succeeded` and becomes the promoted reference. Commit that reference record, then rerun:

```sh
just reproduce --verify
```

A rerun is marked `verified` only when every declared output matches the promoted reference by SHA-256 digest or passes a declared tolerance check. Any difference produces `mismatch`, fails the command, and is never promoted. Strict `just ship-ready` requires a `verified` run record matching the current commit and files on disk.

## DVC staleness and per-stage dependency checks

In a DVC project, `just outputs` (`astack run outputs`) labels every output `current`, `outdated`, `missing`, or `unknown` along with the reason.

- An output is `current` when its stage's `deps`, `params`, and `outs` in `dvc.yaml` still match `dvc.lock` and its last run passed dependency checks.
- Keep `dvc.yaml` deps coarse (for example your whole `src/<pkg>` package, the stage script, `data/raw`, and `uv.lock`) and set `cache: false` on outputs so DVC tracks staleness without duplicating large files.
- astack queries DVC's resolved pipeline graph, so `foreach`, `matrix`, `wdir`, and `.dvcignore` behave as DVC sees them.
- Frozen stages and stages that read undeclared files under `data/interim/`, `data/processed/`, `evidence/`, `artifacts/`, `reports/figures/`, `reports/tables/`, or `scratch/` are labeled `unknown`. Staleness propagates downstream, and `unknown` wins.

During `just reproduce`, astack runs DVC stages one at a time in pipeline order and enforces three checks per stage:

1. **Undeclared writes (all toolchains).** Compares project file metadata before and after the stage, including status-change time (`ctime`), catching any created, modified, or deleted file outside the stage's declared `outs`.
2. **Undeclared reads of derived files (all toolchains).** While a stage runs, every file under `data/interim/`, `data/processed/`, `evidence/`, `artifacts/`, `reports/figures/`, and `reports/tables/` that the stage did not declare as a `dep` or `out` is temporarily made unreadable, then restored. Any library or tool (Python, pyarrow, GDAL, netCDF, R, or shell commands) that tries to read an undeclared derived file fails immediately with the path to declare. `data/raw/` is never locked (declare it by folder). Symlinks, hard-linked files, ACL-readable files, and runs executed as `root` cannot be safely locked and are reported as `inputs-not-locked` (P2). Lock state is journaled in `.cache/input-lock.json` so an interrupted run restores permissions on the next run.
3. **Other Python reads.** A hook loaded into Python processes records opened and imported project files outside `deps` (`.pyc` files map back to their `.py` source). Native libraries that bypass Python's file hooks outside derived directories are not seen by this hook.

Any undeclared read or write is a P1 finding and marks the stage's outputs and everything downstream as `unknown`. Running plain `dvc repro` directly leaves stages marked `unchecked` (P2) until `just reproduce` runs them.

## Non-Python and command-engine pipelines

Any toolchain works with astack. Shell scripts, R scripts, Make targets, and compiled binaries can run as DVC stages or through `engine = "command"`.

Example DVC stage calling R and a shell/Make build step:

```yaml
stages:
  preprocess:
    cmd: make -C pipeline preprocess
    deps:
      - pipeline/Makefile
      - pipeline/src
      - data/raw
    outs:
      - data/interim/analysis/clean.csv:
          cache: false

  fit_model:
    cmd: Rscript scripts/fit_model.R
    deps:
      - scripts/fit_model.R
      - data/interim/analysis/clean.csv
    outs:
      - data/processed/analysis/estimates.csv:
          cache: false
```

Or without DVC, declare a command-engine entry point in `.astack/project.toml`:

```toml
[reproduction]
engine = "command"
component = "analysis"
command = ["bash", "scripts/run_pipeline.sh"]
outputs = ["data/processed/analysis/estimates.csv"]
```

What to expect with non-Python steps:

- Undeclared write detection and derived-file read locking apply to every toolchain.
- A stage whose command is not a single Python process (or that spawns external subprocesses) is disclosed as untraced (P2), and `just report-check` notes its outputs as `reads-not-checked` without failing. Declare those stages' `deps` carefully.
- For toolchain scratch files left behind during execution (such as R's `.Rhistory` or `Rplots.pdf`), list project-relative patterns under `[reproduction] check_ignore`. Ignore patterns can never cover `data/raw/`.
- In `engine = "command"` projects, outputs stay `current` while their run record matches and no committed file other than run records has changed.

## Seeds and numerical tolerances

Declaring `random_seed` in `[reproduction]` passes `ASTACK_RANDOM_SEED` into the pipeline environment and records the seed in every run record. It does not prove your code actually used the seed. Only a `verified` rerun proves repeatable outputs.

If an output is legitimately non-byte-identical across runs (for example, floating-point parallel reductions or embedded build metadata), declare a project-owned tolerance checker in `.astack/project.toml` and explain why in your validation notes:

```toml
[reproduction.tolerance]
"data/processed/analysis/estimates.csv" = [
  "uv", "run", "--locked", "python", "scripts/compare_estimates.py",
]
```

The checker compares the reference and rerun outputs and exits zero only when values agree within your declared scientific tolerance.
