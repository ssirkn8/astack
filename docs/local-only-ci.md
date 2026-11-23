# DS CI with local-only or restricted sources

A CI runner cannot authenticate scientific inputs it cannot access. Engineering CI can still
validate code and source metadata, but it must say explicitly that source identity and full
reproduction were not verified there. This opt-in mode targets Data Science projects;
it does not redefine General project-owned CI or certify a public release.

## Explicit configuration

In `.astack/project.toml`:

```toml
[ci]
mode = "local-only-sources"
```

For each source unavailable on the runner, add this to its existing `data/sources.yml` entry:

```yaml
availability: local_only
```

Keep all existing required provenance, license/redistribution, checksum/identity and sensitivity
fields. Other sources may declare `availability: ci_available` or omit the field; missing such
sources remain blocking. Invalid availability values fail validation. Do not upload private or
restricted inputs to make CI green, and never update a frozen checksum to bless modified data.

The project must retain its original strict local recipe and add the limited CI recipe:

```just
ship-ready:
    uv run --locked python .astack/workflow.py audit --format text --run-tools --strict

ci-check:
    uv run --locked python .astack/workflow.py audit --format text --run-tools --strict --ci
```

Use an active, non-ignored exact `run: just ci-check` step in each applicable validation workflow.
The structural audit accepts it only with the explicit mode and matching recipe. Masked/commented
steps or `|| true` do not establish conformance. Both readiness recipes must now be plain,
single-command recipes: script/interpreter attributes, shebangs, and extra executable lines are
rejected because they can discard a failed audit. Existing installations have project-owned justfiles
and workflows: add the recipe/step deliberately after upgrading the managed runtime, rather than
assuming an upgrade overwrites owner files. New scaffolds include the recipe but do not enable the
mode or change their default strict workflow.

## Claims and failure behavior

The command runs `just check` and `just integration`, validates metadata and every available
source, and reports `ci_validation.scope = engineering-and-metadata-only`. It cannot report a CI
pass without running those checks. Missing explicitly local-only sources and unavailable exact-run
provenance become visible P2 `not-verified-in-ci` findings. Source-dependent optional rendering and
reproduction commands are not run in this limited tier, but capability configuration remains checked.
Checksum mismatches for present data, invalid metadata, undeclared missing data, failed software
checks and other P0/P1 findings still fail CI. No environment variable silently activates this mode.

Even when engineering CI passes, computational reproduction and release readiness stay
`not-assessed`, never `pass`. The local strict readiness predicate rejects the limited result.
Local `just ship-ready` retains full source and current-commit run verification. No ancestor run or
PR merge-parent record is automatically accepted as scientific evidence. A synthetic CI merge
commit therefore does not need to be presented as a scientifically reproduced local commit.

Before scientific acceptance, shipping material outputs, or release publication, run strict local
validation and the project's actual reproduction/interpretive checks against the intended code and
source identities. The CI result is not a waiver of those gates or of owner PR review. This separate
claim is especially important when replacing the legacy lifecycle with vanilla upstream skills.

## Verification

The named local-only reproducer reports all three conflicts on the pre-fix runtime: CI conformance,
missing source and missing run provenance. After the fix, conformance succeeds while strict local
source/run checks still fail, as intended for that fixture. Regression tests execute the generated
CLI and real project-owned check/integration commands on disposable projects, prove the scoped CI
exit and explicit limitations, and keep strict failures for bad checksums, failed software checks,
undeclared sources/policy, missing tool execution and masked recipes. No restricted real input is
included in tests or fetched by CI.
