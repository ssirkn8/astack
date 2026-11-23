---
name: project-policy
description: Select shared runtime, validation, and scientific policy for General and DS projects.
---

# Project policy

For human-response decisions, apply `.opencode/skills/human-decisions/SKILL.md`.

Profiles select policy over the same core runtime, not independent semantics or additional
merge/publication authority.

- Every project uses `.astack/project.toml` and runs the runtime as `python .astack/workflow.py`.
  In a uv project, such as one made by `astack new`, run it as
  `uv run --locked python .astack/workflow.py` so it uses the project environment.
- Validation runs the exact project-owned argv arrays in `[validation]` through `validate <tier>`,
  plus every additional configured tier required by the acceptance criteria. Never infer uv, just,
  pytest, or a Python application scaffold. Missing or unavailable checks block readiness.
- `profile` is `general` or `data-science`. Data Science requires the `scientific-review` and
  `scientific-validation` capabilities and applies scientific provenance, output, reproduction and
  review safeguards. `astack new` creates a Data Science project with `just check` as its CHECK.
  `tools` (dvc by default for new data science projects, marimo always for them, optional quarto) are
  separate from these capabilities.
- Changing profile with `astack change-policy` keeps the same managed files and project-owned
  checks. It does not inject an application scaffold or infer the programming language.
  Active linked worktrees block a canonical policy change.
- Scientific requirements apply to declared or actually affected scientific work in either profile.
  Assess mixed software/science projects honestly. Declarations and `review.scientific_paths` impose
  minimum review applicability, but absent patterns do not establish that a change is nonscientific.

Installation and migration must preserve owner files and checksum ownership. This policy does not
install a profile, authorize consumer changes, or claim cross-platform/native-client acceptance.

## Shared review scrutiny

Use `project_policy.review_policy` on the complete effective changed-file scope when reviewing.
Preserve its conservative risk floor and required axes. Evaluate actual scientific changes and
unknown material factors even when paths or the General profile do not declare them. Low risk never
means implicit readiness or skipped CI. Manual risk may increase, never reduce, the evidence-supported
depth. If an independent reviewer or required evidence is unavailable, report incomplete review.
Never substitute self-approval or treat unavailable telemetry as zero. After implementing or
revising source code, run the bounded comment pass in `.opencode/skills/source-comments/SKILL.md`
before reproduction, `just ship-ready`, and PR review.
