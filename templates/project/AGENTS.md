# Project instructions

This project uses astack. Put your own rules here. astack upgrades never change this file.

## Where things are

- `.astack/` holds the workflow runtime and its manifest. astack manages these files, so do not
  edit them. `.astack/project.toml` is yours: profile, tools, and validation commands.
- Managed skills live in `.agents/skills/`, `.claude/skills/`, and `.opencode/skills/` so each
  agent can discover them. Do not edit managed skill files directly.
- Upgrade with `/astack-upgrade [<tag>]` or `astack run upgrade [<tag>]` (defaults to the latest
  release). It is a dry run until a person approves the exact plan.

## How we work

- One coherent change per branch and PR. Link the GitHub issue with `Closes #N`. If GitHub is
  unavailable, use one file in `docs/work-items/` instead.
- Plan larger work with `/to-spec`, then split it with `/to-tickets`. Both file issues in this
  repository's GitHub tracker (`gh`) labeled `ready-for-agent`. Without GitHub, write one file per
  ticket in `docs/work-items/`, not `.scratch/`.
- Issue labels: `bug`, `enhancement`, or `documentation` for the kind of work, `ready-for-agent`
  (an agent can pick it up), `upstream` (blocked outside the project), and `needs-decision` (a
  person must decide first, including on PRs). Create a missing one with `gh label create`. Add no
  others unless the project already uses them or a person asks.
- When to stop and ask a person is set by `.opencode/skills/human-decisions/SKILL.md`.
- When you verified a change by looking at it, follow `.opencode/skills/pr-visuals/SKILL.md` to
  decide whether the PR should show an image. Most PRs need none.
- After implementation and after review fixes, before opening or updating a PR, follow
  `.opencode/skills/source-comments/SKILL.md`. Straightforward code may need no new comments.
- Never merge, publish, or run `dvc push` unless a person explicitly asks. `full-stop` prepares a
  PR and stops. `full-send` may merge, so use it only when asked by name.
- Public release uses a clean allowlisted export (`publish-public`), never this repository's history.

## Data

- Never change or delete files under `data/raw/`. Record every source in `data/sources.yml`, with
  its license, sensitivity, and a SHA-256 checksum or verifiable identity.
- In a linked worktree, run `astack run share-raw`, then `astack run share-raw --apply`, to share
  raw inputs. Everything else (interim and processed data, reports, scratch) stays in each worktree.

## Science

- Put reusable logic in `src/` and keep `scripts/` thin. Write evidence to `evidence/<component>/`,
  figures to `reports/figures/<component>/`, and tables to `reports/tables/<component>/`.
- Passing tests do not make an analysis valid. Check sampling, missingness, leakage, dependence,
  uncertainty, and sensitivity where they apply, and say what a person still needs to review.
- Scientific outputs are opt-in, so never declare them on your own. When the project declares them,
  follow `/ds-output-contract`: agree the Scientific Output Contract with a person before
  substantial work (headline outputs and required validation, while optional diagnostics may wait),
  put one `Analysis: <key>` line in each ticket or PR that creates or changes declared outputs, and
  run `just ship-ready <key>` before that PR. Deadline pressure never permits required validation to
  be skipped.
- Only a person signs off code, from `just review`. Never call `record_review` or
  `revoke_review`, or write under `.astack/reviews/`. Review requests on a change belong in its PR.
- Give every pipeline stage script a one-line docstring saying what the step does. The review
  notebook shows it as the step's purpose.

## Checks

- `just check` while working, `just integration` for integration tests, and `just ship-ready`
  before opening a PR. `just acquire` fetches inputs, and `just reproduce` reruns the declared
  pipeline.
- For a material pipeline, declare `[reproduction].outputs`. Exit code 0 is only
  `command-succeeded`. Commit, record a reference with `just reproduce --promote`, commit that,
  and rerun: only a matching rerun is `verified`. `just ship-ready` needs a verified record that
  still matches the files on disk.
- A check that is missing, skipped, or unavailable has not passed.

Stop and ask when raw data would change, provenance or licensing is unclear, sensitive data could
leak, checks fail, or you are unsure you have authority to act.
