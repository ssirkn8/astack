# Validation Tiers

Use the validation tiers to separate routine development checks from representative scientific
verification and expensive material reconstruction.

## CHECK

Run `just check` for the fast locked-environment, formatting, linting, typing, non-integration,
configuration, and selected capability checks. Tests marked `integration` run through the explicit
`just integration` target instead. This is the default pull request gate. It does not establish
scientific validity.

CI and any project gate that requires the complete test suite should run both `just check` and
`just integration`. `just test-split` proves that the collected tests are partitioned between the
two gates. Keep integration coverage explicit rather than silently skipping it. A project with no
integration-marked tests has a valid empty integration tier, but a declared integration test must
run and fail the complete gate when it fails.

The reference budgets are 90 seconds for the fast gate, 480 seconds for the integration gate, and
600 seconds for the complete shipping gate. `ValidationSession` enforces the configured budgets
and records timing evidence. Unavailable or failed validation commands are never passes.

### What `just ship-ready` reports

`software_checks` says whether `just check` and `just integration` ran and passed. It is not
reproduction evidence. `computational_reproducibility` stays `not-assessed` while
`analysis.stage` is `exploratory`, and release readiness stays `not-assessed` until reproducibility
passes. An exploratory project is ship-ready when its software checks pass. A material project is
ship-ready only when computational reproducibility passes.

## VERIFY

Validation reuses already-collected timing evidence for a bounded, versioned local diagnostic
history in the shared Git-common workflow state directory. It adds no benchmark, model call,
network request, prompt, score, or shipping gate. History survives ordinary worktree cleanup.
Audit JSON can expose `optimization_observations`, distinguishing unavailable
or insufficient evidence from descriptive observations and recurring patterns. Diagnostic
records exclude commands, errors, arbitrary output, dataset contents, prompts, and local paths.
Keep `just validation-benchmark` an explicit experiment where available, never routine overhead.

VERIFY is an opt-in integration tier for representative multi-step paths. Prefer tiny fixtures and
reusable logic under `src/` over a notebook-only pipeline. Declare one or more direct command arrays
in `.astack/project.toml`:

```toml
[validation]
verify = [["uv", "run", "--locked", "pytest", "-m", "verify"]]
```

Run `just verify`. Leave this tier undeclared when the project has no applicable integration path.
Declared VERIFY commands also run as part of `validate check`. While the tier is undeclared, the
CHECK result reports VERIFY as `not-run`, which is never a pass, and `just verify` fails.
`just ship-ready` runs declared VERIFY too. Undeclared, it blocks once `analysis.stage` is
`material`.

## RELEASE

RELEASE is an opt-in material-work tier for full reconstruction, durable evidence, and strict output
checks. It is not part of the default pull request gate and requires a clean worktree.

```toml
[validation]
release = [
  ["just", "reproduce", "--verify"],
  ["uv", "run", "--locked", "python", ".astack/workflow.py", "audit", "--format", "text", "--run-tools", "--strict"],
]
```

Run `just release`. Missing, skipped, unavailable, or failed commands never count as a passing
tier. Keep durable machine evidence in `evidence/<component>/` and human-facing figures and tables
in their component-scoped report directories.

## Committed-history privacy

Declare project-specific private locations in `.astack/project.toml` when they must be absent from all
reachable Git history:

```toml
[privacy]
private_history_paths = ["client-private", "notes/internal.txt"]
private_history_safe_paths = ["client-private/README.md"]
```

Private roots match descendants by path component. Safe paths match exactly, never as a directory or
prefix. Paths must be relative, normalized, and free of `.` or `..` components. Invalid policy and
unavailable history are blocking findings. This policy is independent of credential scanning and
`data/raw` immutability checks, and omitted policy preserves the default behavior.
