# Project Title

One-line project description.

## Research Context

- Research question or analytical objective: Not yet defined
- Intended use and decision context: INTENDED_USE
- Target population or analytical scope: Not yet defined
- Unit of analysis: Not yet defined
- Consequence if wrong: Not yet assessed

Complete these fields before material analysis. They determine the required scientific review depth.

## Data

Canonical provenance, licensing, sensitivity, redistribution, and acquisition evidence live in
`data/sources.yml`. Locally stored source snapshots under `data/raw/` are immutable.

The data lifecycle is explicit: `data/manual/` contains human-curated inputs, `data/interim/<component>/`
contains mutable intermediate transformations, and `data/processed/<component>/` contains
analysis-ready products. Only `data/raw/` is shared between linked worktrees.

Component outputs belong in component-scoped directories: generated machine outputs in
`artifacts/<component>/`, durable QA and run evidence in `evidence/<component>/`, figures in
`reports/figures/<component>/`, and tables in `reports/tables/<component>/`. Use `scratch/` only for
disposable work. It is not evidence and must not be cited by a report.

## Setup

```bash
just check
```

`just check` exactly synchronizes the locked environment before running the validation tools.

Validation is progressive rather than one universal gate:

- CHECK: `just check` runs fast static, unit, configuration, and selected capability checks.
- Integration tests marked `integration` run with `just integration` and remain an explicit required
  gate when the project has them.
- VERIFY: `just verify` runs opt-in representative fixture-backed integration commands declared in
  `.astack/project.toml`.
- RELEASE: `just release` runs opt-in full material reconstruction and release checks. It requires a
  clean worktree and is never part of the default pull request gate.

Missing or skipped VERIFY and RELEASE commands are not evidence that those tiers passed. Projects
that do not need material validation can leave the optional `[validation]` section absent.

## Workflow

```text
coherent change -> issue(s) -> branch or linked worktree -> ship-ready -> PR -> review -> merge
```

Use `/ds-repo-audit` at analytical milestones and before release. New projects run their pipeline
through DVC, which labels stale outputs. `dvc.yaml` starts with no stages. Add a stage for each step,
with coarse deps (the whole package, the script, `uv.lock`) and `cache: false` outputs. Quarto and
Marimo are present only when selected during project creation.

Projects with private locations that must never appear in committed Git history can opt into the
`[privacy]` section in `.astack/project.toml`. Set `private_history_paths` to project-relative files or
directory roots. Use `private_history_safe_paths` only for exact, narrow exceptions such as a
non-sensitive README inside a private root. Absolute, traversing, repeated-separator, and
cross-platform unsafe paths are rejected. This check supplements credential scanning and raw-source
immutability checks. It does not remove files from existing Git history, so use a clean allowlisted
export for public release.

Scientific output contracts are optional. For material analytical work, declare a
`[scientific_outputs]` registry in `.astack/project.toml` (see `docs/scientific-outputs.md`) and use
`/ds-output-contract` to propose and confirm outputs and validation before substantial
implementation. Without a declaration, results say `not_declared`, not failed. The registry separates
data products, human-facing figures/maps/tables, required validation, and machine-readable evidence.
Human confirmation is
stored with the definition digest it confirmed, so it survives later sessions and is invalidated only
by material definition changes, not by progress or evidence edits. Run
`uv run --locked python .astack/workflow.py output-contract` to inspect the read-only summary.
Deadline pressure may defer optional extras but never required validation or reproducibility.
New ideas normally become follow-up work rather than implicit scope.

astack has no lifecycle engine of its own. Use one coherent branch and PR per change with
the harness's native Git tooling or, in T3 Code, the pinned tstack skills (`full-stop`,
`full-send`, `file-pr`, `babysit-pr`) when installed. In a linked worktree, run
`python .astack/workflow.py share-raw` and then `--apply` to link immutable shared raw inputs.

Pi uses the same project skills through its native `/skill:<name>` commands. `.pi/settings.json`
adds the shared `.opencode/skills` root. Pi must trust the project before loading its local
settings and skills.

## Reproduction

This project begins in the `exploratory` stage and does not contain a fake pipeline. Once results
become material, declare `[reproduction]` in `.astack/project.toml`:

- DVC projects use `engine = "dvc"`, `component`, and a non-empty project-owned `dvc.yaml`.
- Non-DVC or explicitly migrated legacy projects use `engine = "command"`, `component`, and a
  command array.

Both modes also declare `outputs`, the result files or directories the pipeline must write. DVC
projects default to their `dvc.yaml` outputs. Each run records a SHA-256 digest of every declared
output. Both modes expose `just reproduce`. DVC reproduction uses `dvc repro` without `--pull`, so it
consumes retained local inputs and does not silently reacquire mutable upstream data. Routine run
records are written to ignored `.cache/runs/<component>/`.

`just outputs` labels each output `current`, `outdated`, `missing` or `unknown`, with the reason.
`just report-check` fails a report that cites an outdated or missing output, and a final report
that cites a file no recorded run produced. Once `analysis.stage` is material, the audit expects a
Markdown report under `reports/`.

A command that exits 0 is not a reproduction. The first `just reproduce --promote` reports
`command-succeeded` and writes a durable *reference* record. Commit it, then run
`just reproduce --promote` again: the rerun is compared with that reference and reports `verified`
or `mismatch`. A mismatch exits non-zero and is never promoted. `just reproduce --verify` fails
unless a reference exists and matches. For a product that is not byte-reproducible, declare a
project-owned check under `[reproduction.tolerance]` and say why in `docs/validation.md`. It
receives `ASTACK_OUTPUT`, `ASTACK_REFERENCE_RECORD`, and `ASTACK_REFERENCE_SHA256` and exits 0
when the output is within your tolerance. Strict `just ship-ready` validation runs one session
containing CHECK, integration, configured capability checks, and the strict audit. It requires a
promoted, verified record for the current committed state whose outputs still match the files on
disk, so deleted or edited results fail.
Session timing evidence is written under ignored `.cache/validation/` and is published by CI.

Each run record identifies the raw bytes consumed (`inputs`: a digest over every file in
`data/raw/`), the `data/sources.yml` digest, the configuration and lockfile, and the environment that
actually ran: installed distributions, Python, OS and architecture, thread and numeric settings, and
native libraries such as GDAL, PROJ, GEOS, and NumPy's BLAS when they are installed. If
`reproduction.random_seed` is set, astack passes it to the command as `ASTACK_RANDOM_SEED` and records
it. astack does not check that your code uses it, so read the variable and seed every generator
yourself. Only a verified rerun shows that results repeat.

Acquisition is a separate operation. Declare `[acquisition].command` when needed and run it through
`just acquire`. Acquisition may add new retained raw inputs but may not rewrite or delete existing
raw snapshots. Update `data/sources.yml` with the resulting provenance and identity evidence.
The command writes only to `$ASTACK_ACQUISITION_DIR`. astack adds those files to `data/raw`
read-only and reports their SHA-256 digests, without copying existing raw data. It requires
`data/raw` to be read-only first (`chmod -R a-w data/raw`).

## Worktree Data

`data/raw` is immutable and shared as one local copy. `data/manual`, `data/interim`,
`data/processed`, artifacts, reports, scratch state, and `.venv` remain isolated in each worktree.
Use the ignored `.astack/local.toml` for machine-specific worktree, data, and optional DVC cache roots.
By default, canonical and linked worktrees derive one per-repository cache under the user cache
directory. Override it with `[dvc] cache_dir = "/path/to/cache"` or `ASTACK_DVC_CACHE_ROOT`. Populate
and verify raw inputs before worktree use,
then run `chmod -R a-w data/raw` or apply equivalent read-only permissions to the configured external
data root. Worktree creation refuses writable shared inputs.

Use `just dvc-cache-status` for a read-only cache inventory and reclaimability report. Use
`just dvc-cache-gc` for the same conservative dry-run preview. Applying cleanup requires the explicit
`python .astack/workflow.py dvc-cache gc --apply` command, a clean project worktree, and no other active
linked worktrees. Unknown cache ownership or unreachable metadata blocks deletion.
