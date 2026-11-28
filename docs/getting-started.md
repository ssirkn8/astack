# Getting started

> Want a 10-minute end-to-end example with T3 Code or Claude Code first? Start with the [Walkthrough](walkthrough.md), then return here for installation, upgrades, and lifecycle details.

astack has three moving parts:

| Piece | What it is | Where it lives |
| --- | --- | --- |
| The `astack` CLI | A Python tool you install once to create, adopt, inspect, and upgrade projects. | On your machine (`uv tool install`) |
| The project runtime | A self-contained copy of astack's checks vendored into your repo so checks work offline and without the CLI installed. | `.astack/workflow.py` in your project |
| Skills | Markdown instructions read by coding agents (astack skills plus pinned upstream skills). | Mirrored across `.agents/`, `.claude/`, and `.opencode/` |

Inside any astack project, run `astack run <subcommand>` to invoke the project runtime. It delegates directly to the enclosing repository's `.astack/workflow.py` (and uses the synced `.venv` Python in Data Science projects). On machines without astack installed (such as CI), run `uv run --locked python .astack/workflow.py <subcommand>` from the project root in uv projects (or `python .astack/workflow.py <subcommand>` in non-uv projects).

## Creating a new data science project

```sh
astack new my-study
```

In a terminal, `astack new` prompts for any metadata you omit. In scripts or non-interactive shells, pass the flags explicitly:

```sh
astack new my-study \
  --name "My Study" \
  --package my_study \
  --description "Short description of the study" \
  --intended-use "Peer-reviewed paper and replication package"
```

What `astack new` creates:

- A Python package under `src/`, tests, `data/`, `docs/`, `reports/`, `justfile`, `uv.lock`, GitHub Actions CI, and the `.astack/` runtime.
- **DVC by default** to run pipeline stages and label stale outputs. Opt out with `--no-dvc` if you want a plain command-engine pipeline.
- **Marimo (required)** for `notebooks/review.py`, where a person reads the pipeline and signs off code.
- **Optional Quarto** with `--tool quarto` (see [Optional add-ons](optional-addons.md#quarto-reports)).
- **Git initialization** on a `main` branch with no initial commit. If the target directory is already inside a Git repository, `astack new` skips `git init` so monorepos never get nested repos. Pass `--no-git` to skip Git setup explicitly.

New projects start in the exploratory analysis stage (`[analysis] stage = "exploratory"` in `.astack/project.toml`). Reproducibility stays `not-assessed` while you explore. Switch to `stage = "material"` before anyone relies on results for a talk, report, paper, or decision.

## File ownership

astack records the checksum of every file it installs in `.astack/manifest.json`:

| Kind | Examples | Upgrade behavior |
| --- | --- | --- |
| Managed | `.astack/workflow.py`, `.astack/manifest.json`, skills in `.agents/`, `.claude/`, `.opencode/`, `.pi/settings.json` | Replaced only when current bytes still match the recorded checksum. Local edits stop the upgrade instead of being overwritten. |
| Owner-owned | `AGENTS.md`, `README.md`, `pyproject.toml`, `.astack/project.toml`, `data/sources.yml`, `docs/`, `src/`, `tests/` | Never modified by astack after creation. |
| Unknown | Any file astack did not create | Never claimed or overwritten, even if identical to a managed file. |

Put project-specific agent instructions in `AGENTS.md`. Every supported harness reads `AGENTS.md` (including Claude Code), so astack does not create a `CLAUDE.md` and leaves any existing `CLAUDE.md` untouched.

## Installing into an existing repository

Use `astack install` to adopt astack in an existing software or hybrid repository in any language. It adds `.astack/` and managed skills without touching your source code, README, CI, dependencies, lockfiles, data, or Git history.

From inside your repository (or pass `<dir>` explicitly):

```sh
astack install --check "npm test"
```

By default, `astack install` targets the current directory (`.`) and installs the exact published release matching your installed CLI version (`--release vX.Y.Z` overrides the tag). Your installed `astack` CLI must come from that exact published release commit. To run a different release without reinstalling globally, prefix with `uv tool run --from 'git+https://github.com/ssirkn8/astack.git@<verified-release-commit>'`.

- `--check` splits a command string into argv tokens without a shell (for example `--check "npm test"`). Use `--check-argv` when you need an exact JSON argv array (for example `--check-argv '["npm", "test"]'`). Repeat either flag if you need multiple check commands.
- `--initialize` initializes Git and installs workflow files into an empty directory without scaffolding a Python starter template.
- **Dry-run first, always.** `astack install` prints every proposed path, source identity, ownership decision, conflict, and a SHA-256 `plan_digest` without writing anything. Apply by rerunning with the exact same arguments plus `--approve-plan <plan_digest>` (which implies `--apply`, and `--apply --approve-plan` remains accepted) using the `plan_digest` printed by the dry run. Any change to files, Git state, checks, or release source invalidates that digest and refuses to apply.
- Ordinary work in a General project uses only Python's standard library (DVC metadata checks require PyYAML). Because `astack install` adds no `justfile`, run the strict audit with `astack run audit --strict --run-tools`.
- Maintainers testing unpublished changes against disposable repositories must pass `--candidate` instead of `--release`. Candidate mode records candidate status without claiming a published release.

## Upgrading and recovery

Upgrade an existing project to the latest published stable release (or pass an exact tag such as `vX.Y.Z`) from inside the project:

```sh
astack run upgrade
```

That resolves the latest published stable release (or the exact tag you pass) and runs its installer as a dry run, printing a SHA-256 `plan_digest` for the proposed changes. Inspect the plan, then apply with that exact `plan_digest` (`--approve-plan` implies `--apply`, and `--apply --approve-plan` remains accepted):

```sh
astack run upgrade --approve-plan <plan_digest>
```

Projects still on an older vendored `.astack/workflow.py` (such as `v0.1.0`) need an explicit tag and `--apply --approve-plan <plan_digest>` for their first upgrade (`astack run upgrade vX.Y.Z`, then `astack run upgrade vX.Y.Z --apply --approve-plan <plan_digest>`).

Safety rules:

- Any edited managed file blocks the upgrade so your changes are never silently lost.
- An older astack release never rewrites a project installed by a newer release. Roll back with Git instead.
- Each apply holds a short-lived lock and writes a transaction journal, verifying written bytes before updating `.astack/version` and the manifest.
- If an install or upgrade is interrupted, the journal blocks future installs until recovered. Inspect the recovery plan with `astack install --recover` (or pass `<dir>`) and apply with its printed `--approve-plan <plan_digest>`. Recovery restores only recorded workflow paths, preserves independent application edits, and refuses changed workflow files, unknown ownership, or an active/unknown lock owner. (On Windows, lock-owner liveness cannot be verified and therefore blocks automatic lock recovery.)

## Switching profiles (`astack change-policy`)

Every project shares the same runtime and `.astack/project.toml`. Choose a profile based on the project's main product:

- **`data-science`**: the main product is analytical results (findings, figures, maps, a paper). Always enables both `scientific-review` and `scientific-validation`.
- **`general` with scientific capabilities**: mainly software that also produces scientific outputs. Keeps your language, layout, and checks while opting into `scientific-review`, `scientific-validation`, or `[review] scientific_paths`.
- **Plain `general`**: ordinary software making no scientific claims. Science checks stay `not-assessed`, never `pass`.

Switch profile or capabilities later from inside your repository (or pass `<dir>` explicitly) using the same dry-run and `plan_digest` approval flow:

```sh
astack change-policy --profile data-science
```

By default, `astack change-policy` targets the current directory (`.`), uses the exact published release matching your installed CLI version (`--release vX.Y.Z` overrides the tag), and enables both `scientific-review` and `scientific-validation` when switching to `data-science` without explicit `--capability` flags. As with `install`, your installed CLI must match that published release commit (or prefix with `uv tool run --from 'git+https://github.com/ssirkn8/astack.git@<verified-release-commit>'`). Apply with `--approve-plan <plan_digest>` (which implies `--apply`) using the `plan_digest` printed by the preview.

What `change-policy` does and does not do:

- Updates policy configuration and manifest metadata without reinstalling the runtime, changing your workflow version, or rearranging application files.
- Keeps existing `.astack/`, managed skills, and `[validation] check` commands intact.
- Does not install or remove DVC, Marimo, or Quarto.
- Requires all active linked worktrees to finish first. Historical results and failed or unavailable evidence stay untouched.
- Removing a capability (by omitting it from the requested capability list) is shown explicitly in the preview and blocked while scientific declarations, provenance, or review obligations remain. To return to `general` while keeping science checks, pass `--profile general` together with both `--capability` flags.
- Interrupted policy transitions recover through the same `astack install --recover` path.

## Agent harnesses and `astack doctor`

Each coding-agent harness discovers skills in its own folder, so astack writes the same managed skill files to each location (T3 Code orchestrates these harnesses and uses `.agents/skills/`):

| Harness | Reads skills from | Notes |
| --- | --- | --- |
| Codex | `.agents/skills/` | Shared discovery directory also used in T3 Code. |
| Claude Code | `.claude/skills/` | Reads only this directory for skills. |
| OpenCode | `.opencode/skills/`, `.claude/skills/`, `.agents/skills/` | Discovers all three. |
| Pi | `.agents/skills/`, plus `.pi/settings.json` | Active after you trust the project. |

Check that your project's tools, managed skill files across all discovery directories, and configuration are healthy, and see which harness CLIs are installed:

```sh
astack doctor
```

Pass `--harness claude-code` (or `codex`, `opencode`, `pi`) to check and require one specific harness CLI and its configuration. `astack doctor` is read-only. It verifies files and CLI tools on disk, but cannot inspect an agent's internal session state, so confirm skill discovery by asking your agent to list its skills.

Harness-specific behavior to know:

- `grill-me` is a short alias that invokes `grilling` via a Skill tool (available in Claude Code and OpenCode). In Codex or Pi, invoke `grilling` by name.
- `to-spec` and `to-tickets` file GitHub issues labeled `ready-for-agent` using `gh`, or fall back to `docs/work-items/` when GitHub is unavailable. Standard issue labels are `bug`, `enhancement`, `documentation`, `ready-for-agent`, `upstream`, and `needs-decision`.
- `full-stop`, `full-send`, `babysit-pr`, and `sol-loop` are written for T3 Code. Outside T3 Code, `babysit-pr` uses its bundled wait script and `sol-loop` requires a configured second model (if unavailable, it stops and says so).
- To copy pinned upstream skills outside a project (for example into `~/.claude/skills`), preview with `astack skills --dest ~/.claude/skills` and apply with `--apply`.

## Mid-study pivots

Real research changes direction. Hypotheses fail, better data arrives, or a reviewer asks for a different model. astack is built to support pivots cleanly without corrupting prior evidence or silently drifting your outputs.

When your study changes direction mid-project:

1. **Explore in a separate branch or Git worktree.** Keep `main` clean and start a new branch or linked worktree (`git worktree add ...`, then `astack run share-raw --apply` to link read-only raw data). Because `data/interim/`, `data/processed/`, reports, and environments stay worktree-local, exploratory runs cannot overwrite your baseline outputs.
2. **Never edit existing `data/raw/` files.** If the pivot brings in new data, add it under new paths via `just acquire` or a new entry in `data/sources.yml` with its checksum. Leave prior raw snapshots untouched so earlier commits remain auditable.
3. **Track new directions as follow-up issues.** Do not silently tack new figures or models onto an in-flight PR. Open a GitHub issue (or a file under `docs/work-items/`) describing the pivot so scope stays explicit.
4. **Update and reconfirm your output contract if deliverables change.** If you use `docs/scientific-outputs.yml`, edit the analysis definitions deliberately, run `astack run output-contract --analysis <key>`, and record a new human confirmation for the updated `definition_digest` in `evidence/scientific-outputs.yml`. Routine progress updates never invalidate confirmation, while definition edits do on purpose.
5. **Re-baseline reproduction once the new direction becomes material.** When the pivoted outputs will support a paper, report, or decision, confirm `[analysis] stage = "material"`, update `dvc.yaml` and `[reproduction]` outputs in `.astack/project.toml`, commit the changes, run `just reproduce --promote` to record the new reference, commit the promoted run record, and verify it with `just reproduce --verify`.
