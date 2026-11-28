# astack

Coding agents move fast, but by default they leave no trail. Raw inputs get edited in place, sources go undocumented, and reported numbers drift from the code that produced them.

astack is a small set of deterministic checks and agent skills for solo developers and small teams building data science and software projects with coding agents, without enterprise bureaucracy. It works in [T3 Code](https://github.com/pingdotgg/t3code) first, and also in Claude Code, Codex, OpenCode, and Pi. It pins proven engineering skills from [tstack](https://github.com/t3dotgg/tstack) and [Matt Pocock](https://github.com/mattpocock/skills), then adds immutable raw data, machine-readable source provenance, reproducible pipeline checks, human code sign-off, and audits that never count missing evidence as a pass. Nothing runs in the background.

## What changes in your agent loop

| What you ask your agent | Without astack | With astack |
| --- | --- | --- |
| "Clean the outliers and run the analysis" | Raw files can get edited in place or read from undocumented local paths. | `data/raw/` stays read-only and hashed in `data/sources.yml`, and clean-tree `just reproduce --promote` records exact input and output digests. |
| "Write up the findings" | Numbers get pasted into prose and go stale on the next commit. | `ds-report-generation` links every number via JSON pointer, and `just report-check` fails on any mismatch or stale output. |
| "Try a second model in a parallel worktree" | Parallel threads overwrite shared outputs or duplicate gigabytes of raw data. | `astack run share-raw --apply` shares only read-only `data/raw/` while keeping interim data, evidence, and reports worktree-local. |
| "Is this ready to merge?" | Agent sees passing unit tests and says you are ready to ship. | `just ship-ready` checks provenance and reproduction freshness, `just review` requires human-only code sign-off, and `full-stop` opens a PR without auto-merging. |

## Quickstart

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), [just](https://just.systems), and Git. Add an authenticated [GitHub CLI](https://cli.github.com) (`gh`) for PR skills, and a second model provider for `sol-loop` cross-model review (T3 Code provides one).

```sh
uv tool install 'git+https://github.com/ssirkn8/astack'

astack new my-study            # prompts for name, description, and intended use
cd my-study
git add -A && git commit -m "Start from astack"
just check                     # lockfile, format, lint, types, and fast tests
just ship-ready                # full strict audit before opening a PR
```

Open `my-study` in T3 Code or Claude Code and start working. Want to try a complete 10-minute session first (with synthetic GeoTIFF + GeoParquet fixtures, caught mistakes, and human sign-off)? Run `astack tutorial intro` and follow the [Walkthrough](docs/walkthrough.md). To add astack to an existing repository in any language, see [Getting started](docs/getting-started.md#installing-into-an-existing-repository).

## The daily loop

Use `just` for project-owned daily shortcuts, and `astack run` for any command in the vendored astack runtime.

| Command | What it does |
| --- | --- |
| `just check` | Runs lockfile, formatting, linting, type checks, and fast tests while you work. |
| `just reproduce` | Reruns the declared pipeline from a clean commit, checks stage inputs and outputs, and records digests. |
| `just review` | Opens the read-only Marimo review notebook so a person can read each pipeline step and sign off committed code. |
| `just ship-ready` | Runs the strict project audit (checks, provenance, reproducibility, and policy) before a PR. |
| `astack run <cmd>` | Runs any project workflow command from inside a project by delegating to `.astack/workflow.py`. |

## What checks prove and what they don't

A green check proves that the evidence you declared exists, its hashes match, and your configured commands ran and exited zero. It never proves that an analysis is scientifically valid, that a sample is representative, or that a causal claim holds.

Design, leakage, uncertainty, and domain fit require a person. The audit separates automated checks from interpretive review and always leaves scientific credibility to human judgment. See [Checks and audit](docs/checks-and-audit.md#what-the-checks-prove-and-what-they-dont) for the exact boundaries.

## Shipped skills

astack installs its own workflow skills alongside pinned upstream skills (`full-stop`, `sol-loop`, `grill-me`, `to-spec`, `to-tickets`, and others locked to exact commits and checksums) into the folders each agent reads (`.agents/`, `.claude/`, and `.opencode/`).

| Skill | What it does |
| --- | --- |
| `project-policy` | Tells the agent which profile, capabilities, and guardrails apply to the project. |
| `ds-output-contract` | Agrees on headline figures, tables, and required validation with a person before building. |
| `ds-repo-audit` | Runs the project audit and files a sanitized report as a GitHub issue (or local file). |
| `ds-report-generation` | Writes Markdown reports where every figure and cited number traces to repository evidence. |
| `research-visuals` | Builds editable diagrams, scientific figures, captions, and tables with contrast and color-vision checks. |
| `pr-visuals` | Attaches annotated images to a PR only when a visual proves something text cannot. |
| `source-comments` | Enforces checkable invariants in code, prunes narration, and explains non-obvious rationale. |
| `pr-review-pass` | Experimental, opt-in, non-blocking review pass alongside `sol-loop` for protected or scientific paths. |
| `publish-public` | Builds a clean, allowlisted public export with no private Git history or restricted inputs. |
| `astack-upgrade` | Upgrades managed astack files in a project without overwriting your edits. |

Put project-specific rules in your repository's `AGENTS.md`, not inside managed skill files, so upgrades never collide with your customizations.

## Docs

**Using astack**

- [Walkthrough](docs/walkthrough.md): 10-minute end-to-end T3 Code and Claude Code session with GeoTIFF + GeoParquet fixtures, caught mistakes, and `just review` sign-off
- [Getting started](docs/getting-started.md): new projects, existing repos, upgrades, profile transitions, agent setup, and mid-study pivots
- [Data and reproducibility](docs/data-and-reproducibility.md): raw data immutability, `data/sources.yml`, worktrees, and DVC or command pipelines
- [Checks and audit](docs/checks-and-audit.md): `CHECK`, `VERIFY`, and `RELEASE` tiers, scientific audits, evidence limits, and local-only CI
- [Review and reports](docs/review-and-reports.md): Marimo code sign-off, scientific output contracts, traceable reports, and visuals
- [Optional add-ons](docs/optional-addons.md): geospatial checks, experimental PR review lenses, Quarto, DVC remotes, and publication metadata
- [Publishing](docs/publishing.md): clean allowlisted public exports without private Git history

**Reference & contributing**

- [Command reference](docs/commands.md): every `astack` CLI command, `just` target, and `astack run` runtime command
- [Maintainer releases](docs/maintainer-releases.md): PR title rules, Release Please draft proposals, and publishing astack itself
- [Security policy](SECURITY.md): reporting vulnerabilities and the DVC cache boundary

## License

astack is [MIT](LICENSE). Pinned upstream skills keep their own MIT licenses under `integrations/t3/licenses/`. Report security issues as described in [SECURITY.md](SECURITY.md).
