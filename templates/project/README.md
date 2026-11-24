# Project Title

One-line project description.

## Research Context

- Research question or analytical objective: Not yet defined
- Intended use and decision context: INTENDED_USE
- Target population or analytical scope: Not yet defined
- Unit of analysis: Not yet defined
- Consequence if wrong: Not yet assessed

Fill in these fields before material analysis so reviewers know the required depth.

## Quickstart

```sh
just check                     # sync lockfile, format, lint, typecheck, and fast tests
just integration               # run tests marked integration
just review                    # open the read-only Marimo review notebook for code sign-off
just ship-ready                # run the strict project audit before opening a PR
just reproduce                 # rerun the declared [reproduction] pipeline (once configured)
```

Use `astack run <subcommand>` to run any other workflow command (delegates to `.astack/workflow.py`). On a machine without the `astack` CLI (such as CI), replace `astack run <subcommand>` with `uv run --locked python .astack/workflow.py <subcommand>`. Data Science projects include Marimo by default for human code sign-off, enable DVC by default unless created with `--no-dvc`, and add Quarto only when selected with `--tool quarto`.

## Project Layout

```text
data/                          sources.yml, immutable raw/, manual/, interim/<c>/, processed/<c>/
src/                           reusable Python package code (keep scripts/ thin)
notebooks/                     read-only Marimo review notebook (notebooks/review.py)
reports/                       Markdown reports, figures/<c>/, tables/<c>/, and local audits/
artifacts/<c>/                 generated machine outputs (models, predictions, diagnostics)
evidence/<c>/                  durable QA, validation, and promoted run records
docs/                          project guides, decisions, data issues, and publication templates
scratch/                       disposable local work (never counts as evidence)
```

In a linked Git worktree, run `astack run share-raw` and then `astack run share-raw --apply` to link read-only `data/raw/` from the canonical checkout. Every other data, report, and environment directory stays worktree-local.

## Workflow, Outputs, and Privacy

Work in one branch or linked worktree per coherent change, run `just ship-ready`, and open a PR. Once results become material, set `[analysis] stage = "material"` and declare `[reproduction]` in `.astack/project.toml`, commit the pipeline and config, run `just reproduce --promote`, commit the reference record, rerun `just reproduce --promote`, and commit the verified record. Check output freshness with `just outputs` and report links with `just report-check`.

- **Scientific output contracts (optional):** For material work, declare `[scientific_outputs]` in `.astack/project.toml` and use `/ds-output-contract` to agree on data products, human-facing figures/maps/tables, required validation, and machine-readable evidence before building. Inspect the contract with `astack run output-contract`. New ideas normally become follow-up work rather than silent scope growth.
- **Committed-history privacy (optional):** Declare `[privacy]` in `.astack/project.toml` (`private_history_paths` and exact `private_history_safe_paths`) to block restricted paths from appearing anywhere in reachable Git history.
- **DVC cache cleanup (DVC projects):** Inspect the project-private cache with `just dvc-cache-status` and preview cleanup with `just dvc-cache-gc`. Apply cleanup only on a clean single worktree with `astack run dvc-cache gc --apply`.

## Project Docs

- [Data contract](data/README.md): folder roles, `data/sources.yml`, external verification, and `in_place` sources
- [Validation tiers](docs/validation.md): `CHECK`, `VERIFY`, `RELEASE`, CI behavior, and privacy checks
- [Scientific outputs](docs/scientific-outputs.md): output registries, human reading path, and figure standards
- [Data ownership](docs/data-ownership.md): keeping derived inputs durable across temporary worktrees
- [Data issues](docs/data_issues.md) and [Decisions](docs/decisions.md): durable research defects and architectural records
- [Publication handoff](docs/publication/README.md): optional license, citation, and availability statements
