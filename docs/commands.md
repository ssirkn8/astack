# Command reference

Every common agent prompt and public command you can run with the `astack` CLI, inside a generated project's `justfile`, or through `astack run`.

## Common agent prompts

Inside T3 Code, Claude Code, Codex, OpenCode, or Pi, state your goal in plain English. `AGENTS.md` and the installed skills map your intent to the right workflow and deterministic checks:

| What you ask your agent | Skill or workflow triggered | Deterministic check behind it |
| --- | --- | --- |
| "Stress-test this analysis plan before we code" | `grilling` (`grill-me`) | Surfaces design gaps before implementation |
| "Turn this design into GitHub issues" | `to-spec` and `to-tickets` | Files `ready-for-agent` issues (or `docs/work-items/` fallback) |
| "Record this new dataset and baseline a reproducible run" | `AGENTS.md` data and science rules | `astack run checksum`, `data/sources.yml`, and `just reproduce --promote` |
| "Write a report summarizing the current results" | `ds-report-generation` | `just report-check` (JSON-pointer values and saved figures) |
| "Audit this repo and file the findings" | `ds-repo-audit` | `just ship-ready` and `astack run file-audit` |
| "Review sign-off is done. Full stop." | `full-stop` and `sol-loop` | Opens a reviewed PR without merging (`just review` stays human-only) |
| "Build a clean public export" | `publish-public` | `astack run export-public` (allowlisted, secret-scanned, history-free) |
| "Upgrade astack to the latest release" | `astack-upgrade` | `astack run upgrade` (dry-run `plan_digest` before any write) |

See the [Walkthrough](walkthrough.md) for a full 10-minute session showing these prompts and checks in action.

## `astack` CLI

Installed once on your machine (`uv tool install 'git+https://github.com/ssirkn8/astack'`).

| Command | Purpose |
| --- | --- |
| `astack --version` | Print the installed astack version. |
| `astack new <dir>` | Create a Data Science project (interactive prompts in a terminal, or pass `--name`, `--package`, `--description`, `--intended-use`, `--tool quarto`, `--no-dvc`, `--no-git`). |
| `astack install [<dir>]` | Adopt or upgrade an existing repository (`<dir>` defaults to `.`). Dry-run by default. Uses the installed version's release tag unless `--release <tag>` or `--candidate` is passed. Accepts `--check <cmd>`, `--check-argv <json>`, `--initialize`, `--recover`, and `--approve-plan <plan_digest>` (implies `--apply`). |
| `astack change-policy [<dir>]` | Switch profile (`--profile general\|data-science`) or `--capability` flags (`<dir>` defaults to `.`, and `data-science` enables its required capabilities when `--capability` is omitted). Dry-run by default. Uses the installed version's release tag unless `--release <tag>` or `--candidate` is passed, applied with `--approve-plan <plan_digest>` (implies `--apply`). |
| `astack doctor` | Read-only check of project tools, `.astack/` files, all managed skill directories, and detected harness CLIs. Pass `--harness <name>` (`claude-code`, `codex`, `opencode`, or `pi`) to check and require one harness, or `--root <dir>` to inspect another directory. |
| `astack skills` | Verify pinned upstream skills, or copy them outside a project with `--dest <dir>` (dry-run) and `--apply`. |
| `astack run <subcommand> [args...]` | Forward a workflow subcommand to the enclosing project's `.astack/workflow.py` (uses the synced `.venv` Python in Data Science projects). |

## Project `just` recipes

Generated Data Science projects include these `just` targets.

### Core recipes

| Target | Purpose |
| --- | --- |
| `just check` | Lockfile check, sync, test-split check, ruff format/lint, mypy, and fast non-integration tests. |
| `just integration` | Validate test split and run tests marked `integration`. |
| `just test-split` | Prove every test belongs to either fast tests or `integration`, never both or neither. |
| `just test` | Run the full `pytest` suite. |
| `just typecheck` | Run `mypy .`. |
| `just format` | Format and auto-fix lint issues with `ruff`. |
| `just verify` | Run the declared `[validation] verify` tier (`astack run validate verify`). |
| `just release` | Run the declared `[validation] release` tier (`astack run validate release`). |
| `just audit-json` | Run the project audit with `--run-tools` and emit JSON. |
| `just ship-ready [<analysis>]` | Run the strict text audit with `--run-tools` (optionally verifying one declared output-contract analysis). |
| `just ci-check` | Run the limited CI audit (`--run-tools --strict --ci`) for local-only data projects (default in Data Science CI). |
| `just acquire` | Run the declared `[acquisition]` command to stage new raw inputs. |
| `just reproduce [--promote] [--verify]` | Run the declared pipeline from a clean commit and record output digests (`--promote` saves durable reference, `--verify` requires a match). |
| `just report-check [reports...]` | Check that report figures and linked numbers match real, current evidence files. |
| `just outputs` | Label every pipeline output `current`, `outdated`, `missing`, or `unknown` with its reason. |

### Capability recipes

Added automatically by default or optional tools selected during `astack new`:

| Tool | Target | Purpose |
| --- | --- | --- |
| Marimo (default) | `just review` | Open `notebooks/review.py` read-only for human code sign-off. |
| Marimo (default) | `just review-check` | Validate `notebooks/review.py` with `marimo check`. |
| DVC (default unless `--no-dvc`) | `just dvc-status` | Show DVC pipeline status (`astack run dvc-status`). |
| DVC (default unless `--no-dvc`) | `just dvc-cache-status` | Inspect the project-private DVC cache (`astack run dvc-cache status`). |
| DVC (default unless `--no-dvc`) | `just dvc-cache-gc` | Preview DVC cache cleanup (`astack run dvc-cache gc`). |
| Quarto (`--tool quarto`) | `just report` | Render `reports/report.qmd` with Quarto. |

## Project runtime commands (`astack run`)

Run these from anywhere inside an astack project. Every command delegates to `.astack/workflow.py`. On a machine without the `astack` CLI installed (such as CI), run from the project root with `uv run --locked python .astack/workflow.py <subcommand>` in uv projects (or `python .astack/workflow.py <subcommand>` in non-uv projects).

| Command | Purpose |
| --- | --- |
| `astack run audit [--format text\|json] [--run-tools] [--strict] [--analysis <key>] [--ci]` | Check workflow conformance, provenance, reproducibility, and configured tools. |
| `astack run validate <check\|verify\|release>` | Run one declared validation tier from `.astack/project.toml`. |
| `astack run reproduce [--promote] [--verify]` | Run the declared reproduction pipeline, enforce input/output rules, and record digests. |
| `astack run acquire` | Run the declared `[acquisition]` command in a staging directory and add new read-only files to `data/raw/`. |
| `astack run outputs` | Label each pipeline output `current`, `outdated`, `missing`, or `unknown`. |
| `astack run report-check [reports...]` | Verify report figures and JSON-pointer linked numbers against evidence (defaults to all reports in `reports/`). |
| `astack run output-contract [--analysis <key>]` | Print the declared scientific output contract summary for one analysis or the whole project. |
| `astack run review-status [--require-reviewed]` | Show every pipeline step's human sign-off status. `--require-reviewed` exits non-zero unless all steps and shared files are signed off at their current bytes. |
| `astack run review-policy [--root <dir>] [--base <ref>] [--risk <level>] [--scientific-changes] [paths...]` | Classify changed or explicit paths for risk floor, `protected_paths`, `scientific_paths`, `required_axes`, and active review lenses. |
| `astack run checksum <path>` | Print `sha256:<hex>` for a file or `sha256-tree-v2:<hex>` for a directory to record in `data/sources.yml`. |
| `astack run verify-source <source-id> <retrieved-path>` | Hash retrieved bytes for an external source and write `data/source-verification/<source-id>.json`. |
| `astack run share-raw [--apply]` | Preview or link read-only `data/raw/` from the canonical checkout into a linked Git worktree. |
| `astack run export-public --out <dir>` | Write an allowlisted, secret-scanned, history-free public export of the committed project. |
| `astack run file-audit --report <path> --commit <sha> --scope <scope> --actionable-count <n> [--to auto\|github\|local\|none] [--remove-on-success]` | File an audit report as a GitHub issue, or keep it locally when GitHub is unavailable. |
| `astack run upgrade [<tag>] [--approve-plan <plan_digest>]` | Fetch the latest published stable astack release (or an exact `<tag>`) and plan or apply a checksum-safe upgrade (`--approve-plan` implies `--apply`). |
| `astack run dvc-status` | Show DVC pipeline status (DVC projects only). |
| `astack run dvc-cache <status \| gc [--apply]>` | Inspect or garbage-collect the project's DVC cache (DVC projects only). |
