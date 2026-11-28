# Validation Tiers

astack separates fast daily checks from representative scientific verification and expensive full-project release gates. One rule applies at every tier:

**Missing, skipped, unavailable, or failed commands never count as a pass.**

## CHECK

Run `just check` on every edit for locked-environment sync, formatting, linting, type checking, and fast non-integration tests. In projects with Marimo, `astack run validate check` also runs `just review-check`.

Keep slow tests marked `integration` out of `just check` and run them with `just integration`. Run `just test-split` to confirm every test belongs to either fast tests or `integration`. An empty integration tier is valid when no integration tests exist, while any declared integration test must pass in the complete gate.

Passing `CHECK` proves engineering checks ran cleanly. It does not prove pipeline reproduction or scientific validity.

## VERIFY

`VERIFY` is an opt-in tier for representative multi-step scientific checks on tiny fixtures (preferring reusable code in `src/` over notebook-only logic). Declare direct command arrays under `[validation]` in `.astack/project.toml`:

```toml
[validation]
verify = [["uv", "run", "--locked", "pytest", "-m", "verify"]]
```

Run it with `just verify` (or `astack run validate verify`). When declared, `VERIFY` also runs during `astack run validate check` and strict `just ship-ready`.

While `verify` is undeclared, `CHECK` reports `VERIFY` as `not-run` (never a pass) and `just verify` fails. Undeclared `VERIFY` is advisory during `exploratory` work and blocks `just ship-ready` once `analysis.stage` is `material`.

## RELEASE

`RELEASE` is an opt-in gate for full reconstruction, strict auditing, and human code sign-off. It requires a clean Git worktree and never runs in default PR CI.

```toml
[validation]
release = [
  ["just", "reproduce", "--verify"],
  ["astack", "run", "audit", "--format", "text", "--run-tools", "--strict"],
  ["astack", "run", "review-status", "--require-reviewed"],
]
```

Run it with `just release` (or `astack run validate release`). On a machine without the `astack` CLI installed, replace `["astack", "run", ...]` with `["uv", "run", "--locked", "python", ".astack/workflow.py", ...]`. Keep durable machine evidence in `evidence/<component>/` and human-facing figures and tables under `reports/`.

## Local vs. CI Runs

- **Local (`just ship-ready`):** runs `CHECK`, `just integration`, configured capability checks, and the strict audit in one session (writing timing records under gitignored `.cache/validation/`). `software_checks` covers `just check` and `just integration` only. `computational_reproducibility` stays `not-assessed` while `analysis.stage` is `exploratory` and must pass once `analysis.stage` is `material`.
- **Default PR CI (`just ci-check`):** Data Science projects default to `[ci] mode = "local-only-sources"` in `.astack/project.toml` and run `just ci-check` in `.github/workflows/checks.yml` (never `just release`). In this mode, gitignored `data/raw/` snapshots and `storage: in_place` sources default to `local_only` (marked P2 `not-verified-in-ci` when absent in CI), while code checks, integration tests, metadata checks, and checksums on present files still run. Set `availability: ci_available` on any source that must exist in CI, or remove `[ci]` and run `just ship-ready` if every source is available in CI. Local strict `just ship-ready` remains required before shipping material results.

## Committed-History Privacy

Declare project-specific private paths in `.astack/project.toml` when they must never appear anywhere in reachable Git history:

```toml
[privacy]
private_history_paths = ["client-private", "notes/internal.txt"]
private_history_safe_paths = ["client-private/README.md"]
```

Private roots match all descendants by path component. Safe paths match exact files only, never a directory or prefix. Paths must be relative, normalized, and free of `.` or `..` segments. Invalid policy or unavailable Git history blocks the audit. This check supplements credential scanning and `data/raw/` immutability checks without modifying existing history.
