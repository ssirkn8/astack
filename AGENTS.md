# astack Maintainer Contract

This repository authors and tests astack, an agent workflow for personal and small-team software and data science projects.

## Canonical sources

- `.opencode/skills/` and `.opencode/commands/` are the only authored OpenCode sources.
- `templates/project/` contains project-owned scaffold files, not copied OpenCode files.
- `workflow/components.json` is the generated-component inventory.
- `src/astack/` generates projects, upgrades managed files, and inspects native Git state.
- `workflow/project_tool.py` is copied into generated projects as `.astack/workflow.py`.
- `pyproject.toml` is the sole workflow version authority.

Never manually maintain duplicate workflow mirrors. Regenerate them through the scaffold or upgrade tooling.

## Maintainer workflow

Use the unchanged, pinned upstream skills under `.agents/skills/` for T3 Code work. Claude Code
reads only `.claude/skills/`, so `scripts/generate_adapters.py` keeps byte-identical copies there.
Default to `full-stop` for maintainer PRs that change astack's behavior. Run `sol-loop` before
opening or handing off a behavior-changing PR and record the verdict in the PR body. This applies
to changes in source, workflow, templates, scripts, publication files, and skills. Docs-only and
typo fixes can use the normal PR path. If Sol is unavailable, say so in the PR body and report
what review was completed. Include this review requirement in maintainer implementation briefs.

`full-stop` leaves the PR open. Use `full-send` only when the owner asks for it by name. The owner
approves and merges the exact PR head for ordinary work. Never merge or publish without the
owner's authorization.

When a change affects installation, upgrades, or a shipped workflow, use the existing acceptance
setup to install or upgrade the candidate in a disposable General or data science project, as
appropriate. Run the changed workflow through its normal project entry point. For a shipped skill
change, invoke it from the consumer project in the affected harness to check discovery and
execution. Record what ran and any unavailable checks in the PR.

Use native Git/T3 worktrees and GitHub issues; `to-spec` and `to-tickets` publish to this
repository's issues with the `ready-for-agent` label. Other issue labels: `bug`, `enhancement`,
`documentation`, `upstream` (blocked outside this repo), and `needs-decision` (the owner must
decide). Do not recreate a registry, lifecycle state machine, or duplicate upstream orchestration.
When a PR change was verified by looking at it, `pr-visuals` decides whether to attach an image.
After implementation and after review fixes, before opening or updating a PR, follow
`.opencode/skills/source-comments/SKILL.md`. Straightforward code may need no new comments.
Keep raw inputs immutable and other products worktree-local.
Before removing a worktree, prove required data and evidence are preserved and disclose blockers.

The retained `.opencode/` skills describe deterministic installation, scientific validation,
audit, reports, and safe public export. Generate their discovery wrappers through tooling.
Do not edit pinned upstream bytes or manually maintain generated copies.

## Design rules

1. Every required artifact must prevent a named failure. Remove it when that justification disappears.
2. Automate objective checks and label interpretive scientific review honestly.
3. Never treat missing, skipped, or unavailable evidence as a pass.
4. Keep data provenance machine-readable in `data/sources.yml`.
5. Keep raw source snapshots immutable when stored locally. External sources require acquisition and verifiable identity evidence. Unknown or unverified evidence never passes release checks.
6. Data science projects run their pipelines through DVC by default, so stale outputs are labeled.
   Marimo is required for them too, because the review notebook is where a person signs off code.
   DVC data storage and Quarto are optional capabilities, not baseline requirements.
7. GitHub issues are the primary work tracker. Local work-item files are a fallback, not a second synchronized tracker.
8. Keep remote-destructive actions separate from read-only review and Git-write workflows.
9. Public release uses a clean allowlisted export or new repository, never a branch that retains private history.
10. Do not add enterprise governance to the personal and small-team baseline.
11. Standard projects share only immutable `data/raw` across worktrees. Interim and processed data, reports, scratch state, and environments remain worktree-local.

## Editing and validation

- Preserve user-owned project files during upgrades.
- Replace a managed file automatically only when its current checksum matches the installed manifest.
- Default upgrades to dry-run.
- Run `just check` after changes.
- Run scaffold and upgrade integration tests when managed files change.
- Keep GitHub tests mocked. Tests must not create live issues or repositories.

## Release policy

Use semantic versions and migration notes for breaking workflow changes. Update generated `.astack/version` files only after a successful install or upgrade.

PR titles become squash subjects and must be Conventional Commits. Intermediate branch commits need not be. Release Please only drafts proposal PRs. See `docs/maintainer-releases.md`. This convention applies to astack itself, not to projects installed with astack.
