# Validation Tiers

The workflow separates fast engineering feedback from representative scientific verification and
expensive material reconstruction. A tier is evidence only when its declared commands actually run
and succeed.

## CHECK

`just check` is the fast development gate. It covers the locked environment, formatting, linting,
typing, non-integration tests, and baseline project checks. Tests marked `integration` are kept out of
this loop so slower integration coverage does not make routine edits approach the CI timeout.

Run `just integration` for the explicit integration tier. The complete maintainer gate uses one
validation session to run `just check`, `just integration`, and `just scaffold-smoke` once each.
The session reuses only successful, input-fingerprinted test-split collection within that
invocation. A failed, missing, or stale collection is never treated as a pass.

Generated projects use the same session contract through strict `just ship-ready`. It runs the
CHECK and integration commands, each configured DVC, Quarto, or Marimo check, and the strict audit
once per session. The session writes ignored JSON timing evidence under `.cache/validation/` and
publishes it as a CI artifact when running in GitHub Actions. Standalone `just check`,
`just integration`, and `just scaffold-smoke` remain available with their existing tier boundaries.

The repeatable budgets are 90 seconds for `just check`, 480 seconds for
`just integration`, and 600 seconds for the complete shipping gate. One session enforces these
budgets and records stage status, wall time, CPU time, a session-wide memory peak, invocation
counts, test counts, commit identity, lock and configuration fingerprints, and tool versions. CI
publishes the report and fails when timing evidence is unavailable or exceeds the budget.

Use `just validation-benchmark` to compare the same fast-gate test selection in serial and parallel
mode. It writes one JSON report per mode under `.cache/validation/benchmarks/`, including the exact
commit and lock/configuration fingerprints, wall time, child-process CPU time, process-tree memory
peak, return code, and pass/fail status. The two reports are produced in separate processes so
memory measurements are not cumulative across modes:

```bash
just validation-benchmark
```

The benchmark's serial and parallel reports must have matching commit and configuration
fingerprints, and both commands must pass against the same `pytest -m "not integration"` test
selection before comparing performance. A routine development check should remain materially below
the prior multi-minute baseline. Full integration coverage must remain a required CI,
shipping, or release gate.

CHECK does not establish scientific validity, reproduce a material result, or replace domain review.

## VERIFY

VERIFY is an opt-in moderate-cost integration tier. It should run representative multi-step paths
using tiny fixtures where the project has data or scientific code that benefits from them. The
commands should exercise reusable code in `src/` and keep notebooks and reports as consumers of
those results.

Declare direct command arrays in `.astack/project.toml`:

```toml
[validation]
verify = [["uv", "run", "--locked", "pytest", "-m", "verify"]]
```

Run it with `just verify`. The baseline does not invent a pipeline. Without the
`scientific-validation` capability, exploratory projects need not declare this tier. With it,
which includes every `data-science` project, CHECK also runs the declared VERIFY commands, and
their failure fails CHECK. While VERIFY is undeclared, CHECK still runs and its JSON result
reports `"verify": {"status": "not-run"}`. Not run never counts as passed, and `just verify`
itself fails until the tier is declared.

Strict `just ship-ready` runs the declared VERIFY commands after CHECK and integration. While
VERIFY is undeclared, the audit reports a `verify-undeclared` finding. It is P2 for exploratory
work and P1 once `analysis.stage` is `material`, so it blocks shipping material results.

## RELEASE

RELEASE is an opt-in expensive tier for material work. It should run the declared full
reconstruction, verify durable run evidence, and perform strict workflow and output checks. Keep it
out of routine pull requests unless the project explicitly chooses that cost.

```toml
[validation]
release = [
  ["just", "reproduce", "--verify"],
  ["uv", "run", "--locked", "python", ".astack/workflow.py", "audit", "--format", "text", "--run-tools", "--strict"],
]
```

Run it with `just release`. RELEASE requires a clean Git worktree. Missing, skipped, unavailable, or
failed commands never count as a passing tier.

## Boundaries

- Existing projects without `[validation]` remain valid.
- Marimo, DVC, Quarto, and fixture-backed VERIFY paths remain optional capabilities.
- Marimo notebooks are human-facing review surfaces, not production pipelines or the sole QA record.
- Machine evidence remains under `evidence/<component>/`, generated outputs under
  `artifacts/<component>/`, and report figures and tables under their component-scoped directories.
- Scientific credibility and domain validity still require interpretive review.

## General and Data Science project policy

Both profiles share one runtime and one configuration file, `.astack/project.toml`. A missing
`profile` field means `general`. A `data-science` project must declare the `scientific-review` and
`scientific-validation` capabilities, and `astack new` writes them. A General configuration looks
like this:

```toml
schema_version = 1
profile = "general"

[validation]
check = [["npm", "test"]]
verify = [["npm", "run", "integration"]]

[review]
scientific_paths = ["exports/scientific/**"]
```

Command arrays are exact argv, executed from the project directory without an implicit shell.
`python .astack/workflow.py validate check` runs the configured checks. General does not infer a build system or add application dependencies.
Missing, malformed, unavailable, timed-out, and unsuccessful commands block validation.
CHECK runs `[validation] check` in either profile. When the project has the
`scientific-validation` capability, which every `data-science` project has, CHECK also runs
`[validation] verify` when it is declared, and reports VERIFY as `not-run` while it is not. Generated DS projects declare
`check = [["just", "check"]]`. VERIFY and
RELEASE run only their declared commands, and RELEASE also requires a clean Git worktree.

General worktrees do not create DS data/report directories or share raw data by default. Explicit
sharing still uses the existing immutable input checks.

Acceptance and engineering/reproducibility review always apply. Scientific review is required
when declared by `review.scientific = true`, matched by `review.scientific_paths`, or when a
scientific output contract is declared. The same output-contract reader enforces declared science
in either profile. A reviewer must also assess scientific effects outside configured patterns,
which remain an interpretive responsibility. An absent declaration does not prove an absence of
scientific impact. A General annotation application can require scientific export review without
inheriting an entire DS scaffold.

Core/profile tests demonstrate representative policy, exact validation, and vendored runtime
behavior. They do not certify arbitrary application stacks, native client behavior, or
cross-platform installation.
No profile grants merge, release, publication, or destructive-action authority.
