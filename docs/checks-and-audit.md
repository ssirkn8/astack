# Checks and audit

Passing tests prove your code ran, not that your analysis is right. astack separates fast engineering checks from representative scientific verification, full material reproduction, and human domain review, and enforces one core rule throughout:

**Missing, skipped, unavailable, unconfigured, or failed checks never count as a pass.**

## Validation tiers (`CHECK`, `VERIFY`, `RELEASE`)

Declare commands as exact argv arrays (executed from the project root without a shell) under `[validation]` in `.astack/project.toml`:

| Tier | Command | Purpose | Default in `astack new` |
| --- | --- | --- | --- |
| **CHECK** | `just check` / `astack run validate check` | Fast feedback on every edit: lockfile, formatting, lint, types, non-integration tests, and `review-check`. | `[["just", "check"], ["just", "review-check"]]` |
| **VERIFY** | `just verify` / `astack run validate verify` | Opt-in moderate-cost multi-step checks on small representative fixtures. | Not configured |
| **RELEASE** | `just release` / `astack run validate release` | Opt-in expensive gate: full reproduction, strict audit, and human code sign-off. Requires a clean Git worktree. | Not configured |

Example `[validation]` configuration for a Data Science project:

```toml
[validation]
check = [["just", "check"], ["just", "review-check"]]
verify = [["uv", "run", "--locked", "pytest", "-m", "verify"]]
release = [
  ["just", "reproduce", "--verify"],
  ["uv", "run", "--locked", "python", ".astack/workflow.py", "audit", "--format", "text", "--run-tools", "--strict"],
  ["uv", "run", "--locked", "python", ".astack/workflow.py", "review-status", "--require-reviewed"],
]
```

And for a General project (for example Node or Rust):

```toml
schema_version = 1
profile = "general"

[validation]
check = [["npm", "test"]]
verify = [["npm", "run", "integration"]]

[review]
scientific_paths = ["exports/scientific/**"]
```

How tiers behave:

- **Test separation.** Keep slow tests marked `integration` out of `just check`. Run them with `just integration`, and verify every test belongs to exactly one tier with `just test-split`.
- **`VERIFY` with `scientific-validation`.** Every `data-science` project (and any `general` project with `scientific-validation`) automatically runs declared `verify` commands during `astack run validate check` and strict `just ship-ready`. While `verify` is undeclared, CHECK still succeeds on its own commands while recording `"verify": {"status": "not-run"}`, and the audit emits `verify-undeclared` (P2 during `exploratory`, P1 blocking once `analysis.stage` is `material`). Calling `just verify` directly fails until `verify` is configured.
- **Default CI stays fast.** Generated GitHub Actions CI runs `just ship-ready` (or `just ci-check` in local-only mode) and never runs expensive `just verify` or `just release` unless you explicitly opt in. Strict `just ship-ready` runs CHECK, integration, configured DVC/Marimo/Quarto/spatial checks, and the audit once in a single validation session, writing timing evidence to `.cache/validation/`.
- **Maintainer timing budgets.** In the astack repository itself, one validation session enforces repeatable budgets of 90s for `just check`, 480s for `just integration`, and 600s for `just ship-ready`. Use `just validation-benchmark` to compare serial vs. parallel fast-gate timings cleanly in separate processes.

## What the checks prove and what they don't

| A green... | Proves | Does **not** prove |
| --- | --- | --- |
| `just check` | Code is formatted, typed, and fast tests pass | Anything about the science |
| Raw checksum | Local snapshot matches `data/sources.yml` byte for byte | That the source data is accurate or fit for purpose |
| External source `verification` record | Supplied local bytes were hashed, bound to the declared source record, and checked against any declared digest | That those bytes came from the declared URL or that the upstream URL will serve the same bytes later |
| `software_checks: pass` | Configured check and integration commands ran and exited zero | Anything about pipeline reproduction |
| `computational_reproducibility: pass` | A material analysis has valid, verified reproduction evidence | That the analytical outputs are scientifically correct |
| `just reproduce` -> `command-succeeded` | Pipeline ran from a clean commit, left raw data untouched, and wrote declared outputs with recorded digests | That a second run produces the same outputs |
| `just reproduce` -> `verified` | A rerun matched the promoted reference by digest or declared tolerance checker | That the outputs are scientifically correct |
| `random_seed` in a run record | Seed was exported as `ASTACK_RANDOM_SEED` and recorded | That your code used it (only a verified rerun proves repeatability) |
| `inputs` in a run record | Exact raw bytes present when the pipeline ran | That those are the right inputs for the question |
| `environment` in a run record | Packages and native libraries of the launched Python interpreter | Environment details for non-Python commands (`unavailable`) |
| `data/spatial.yml` + validators | Declared spatial metadata is complete and project validators exited zero | That the CRS, resolution, or resampling suits the question |
| `[publication]` metadata | License, citation, and availability files exist, are complete, and any DOI has valid syntax | That the DOI resolves to this release or that licenses are compatible |
| `astack run export-public` | Only allowlisted, committed, source-permitted, secret-scanned files were exported without history | That exported science is correct or free-text licenses were interpreted accurately |
| `just report-check` | Every image and numeric link points at a real, current file under `evidence/`, `artifacts/`, `reports/figures/`, or `reports/tables/`, JSON pointers match at shown decimals, strict Markdown rules hold, and final reports have no `[UNRESOLVED` markers | That unlinked prose or unlinked numbers are supported, that live `.qmd` cell plots are checked, or that the science is right |
| Output contract `confirmed` | A person approved that definition of outputs and required validation | That the produced outputs actually satisfy it |
| `scientific_credibility` | Nothing. Always `needs-interpretive-review`. | Always requires human domain judgment |

## Scientific audit (`ds-repo-audit`)

Run the deterministic audit with `just audit-json` (machine-readable), `just ship-ready` (strict human-readable), `just ship-ready <analysis>` (also checks one declared output-contract analysis), or `astack run audit --format text --run-tools --strict`.

Every audit reports five verdicts separately:

| Verdict | Driven by |
| --- | --- |
| `workflow_conformance` | Required files, manifest checksums, layout, and policy configuration |
| `software_checks` | Whether configured check and integration commands ran and passed (`--run-tools`) |
| `computational_reproducibility` | Verified reproduction evidence for `material` analyses (`exploratory` stays `not-assessed`, and passing software checks never count) |
| `scientific_credibility` | Always `needs-interpretive-review` |
| `release_readiness` | Provenance completeness, licensing, redistribution, and all verdicts above (`not-assessed` until reproducibility passes) |

Findings are prioritized from **P0** (data safety, such as a modified raw file) to **P3**.

- **Filing audit issues.** The `ds-repo-audit` skill runs the audit and files a sanitized summary through `astack run file-audit` as a GitHub issue, or keeps it in gitignored `reports/audits/` when GitHub is unavailable.
- **General projects.** Audits in a plain General project check managed files, secrets and large files, private history, a clean worktree, and (with `--run-tools`) your `[validation] check` commands. They never ask for `uv.lock`, `pyproject.toml`, or a `justfile`. Science checks activate only when you opt in through a scientific capability, `[review]`, `[scientific_outputs]`, `[analysis]`, or `data/sources.yml`.
- **Raw immutability in CI.** The audit compares `data/raw/` against the PR base merge-base (generated CI uses `fetch-depth: 0` so full history is available). On pushes, CI passes `ASTACK_AUDIT_BASE_SHA` from `github.event.before`. Initial pushes (all-zero SHA) or malformed/missing SHAs fail closed as unavailable evidence rather than silently passing.

### Interpretive scientific review

Automated checks make an analysis reviewable. They do not judge scientific validity. For material analyses, reports, model or validation changes, public releases, sensitive data, or high-stakes decisions, a person must review:

- research question, intended use, target population, unit of analysis, and scope
- data collection, coverage, representativeness, bias, and measurement error
- missingness, exclusions, duplicates, outliers, data defects, and train/test leakage
- validation splits suited to temporal, grouped, spatial, or repeated-measure dependence
- assumptions, baselines, diagnostics, calibration, uncertainty, effect sizes, multiple testing, and sensitivity
- boundaries between exploratory, descriptive, predictive, and causal claims
- stochastic and environment reproducibility
- external validity, limitations, privacy, fairness, and foreseeable misuse
- traceability from every report claim to sources, code, parameters, QA, and outputs

Interpretive findings use one of five states:

```text
Supported | Concern | Missing evidence | Not applicable | Needs domain review
```

An audit surfaces evidence gaps and review needs. It never replaces a qualified statistician, methodologist, domain expert, ethics review, or data steward.

## Local-only CI (when data cannot leave your machine)

When your project uses private or restricted inputs that GitHub Actions cannot access, never upload restricted data just to make CI green. Opt into limited engineering-and-metadata CI instead.

1. Enable local-only mode in `.astack/project.toml`:

```toml
[ci]
mode = "local-only-sources"
```

2. Mark each unavailable source in `data/sources.yml` while keeping all required provenance, checksum, license, and sensitivity fields:

```yaml
availability: local_only
```

Sources without `availability` (or marked `ci_available`) remain blocking if missing in CI.

3. Keep your existing strict local `ship-ready` recipe intact (including its optional `analysis=""` parameter) and ensure `justfile` has the limited `ci-check` recipe (new scaffolds already include it):

```just
ci-check:
    uv run --locked python .astack/workflow.py audit --format text --run-tools --strict --ci
```

Then call `run: just ci-check` as an exact, active step in `.github/workflows/checks.yml`. Both `ship-ready` and `ci-check` must stay plain single-command recipes (shebangs, extra commands, commented steps, or `|| true` are rejected by the audit).

**What local-only CI proves and refuses to claim:**

- Runs `just check`, `just integration`, metadata checks, and checksum checks on any sources present in CI, reporting `ci_validation.scope = engineering-and-metadata-only`.
- Missing `local_only` sources and unavailable exact-commit run records appear as visible P2 `not-verified-in-ci` findings.
- Checksum mismatches on present data, malformed metadata, missing non-local-only sources, failed software checks, and any P0/P1 findings still fail CI.
- `computational_reproducibility` and `release_readiness` stay `not-assessed`, never `pass`. Local strict `just ship-ready` and reproduction remain mandatory before shipping material results or publishing.
