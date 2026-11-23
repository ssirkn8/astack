# How astack works

This page covers every part of `astack` in plain terms. Each section explains one moving part:
what it is, why it exists, and what it does not do. If you only read one section, read
[What the checks prove, and what they don't](#what-the-checks-prove-and-what-they-dont).

## The idea in one paragraph

Coding agents are fast, but by default they leave no trail. Nothing records where the data came
from, whether the raw files were changed, or whether a "result" was ever rerun. `astack` is a small
set of files you add to a project. They give an agent the rules, and give you checks that read
evidence instead of trusting the agent's summary. The goal is a project that a reviewer, such as a
journal referee, can clone, rerun, and audit without having to trust you or the agent.

## The three pieces

| Piece | What it is | Where it lives |
| --- | --- | --- |
| **The `astack` tool** | A Python CLI you install once. It creates, adopts, and upgrades projects. | On your machine (`uv tool install`) |
| **The project runtime** | A self-contained copy of `astack`'s checks, vendored into each project so the project works offline and without `astack` installed. | `.astack/` in your project |
| **Skills** | Markdown instructions agents read: how to audit, write a report, publish, and so on. | `.opencode/skills/`, mirrored for each agent harness |

`astack` never runs in the background. Nothing happens unless you or your agent runs a command.

## Lifecycle of a project

```text
1. create or adopt    astack new / astack install          (dry-run, then apply)
2. record your data   data/raw/ + data/sources.yml          (immutable, checksummed)
3. work               branch -> change -> just check -> PR  (agent or you)
4. audit              just audit-json / just ship-ready     (evidence-based verdicts)
5. reproduce          just reproduce [--promote]            (clean-tree rerun + run record)
6. publish            publish-public skill                  (clean allowlisted export)
7. upgrade            .astack/workflow.py upgrade vX.Y.Z    (dry-run, checksum-safe)
```

## 1. Creating or adopting a project

- `astack new <dir> --name ... --package ... --description ... --intended-use ...` writes a
  data science project: a Python package under `src/`, tests, `data/`, `docs/`, a `justfile`,
  `uv.lock`, CI, and the `astack` runtime. It runs `git init` on a `main` branch and makes no
  commit. Inside an existing Git repository it skips that step, so a monorepo never gets a nested
  repository. `--no-git` turns it off. Run in a terminal, it asks for any detail you leave out.
- `astack install <existing-repo> --release vX.Y.Z --check-argv '["npm","test"]'` adds `astack` to a
  repository you already have, without touching your source, README, CI, dependencies, or data.
  `--check-argv` tells `astack` which command is your project's "check".
- `astack new` adds DVC, which runs the pipeline and labels stale outputs (`--no-dvc` opts out),
  and Marimo, which is required because the review notebook is where a person signs off code.
  `--tool quarto` adds Quarto.

**Dry-run first, always.** `install`, `change-policy`, and `upgrade` print a plan and a plan digest,
and change nothing. To apply, rerun with `--apply --approve-plan <digest>`. If anything changes
between the dry run and the apply, the digest no longer matches and the apply refuses. Those
changes include files, Git state, and the release.

### Profile and analysis stage

Two settings in `.astack/project.toml` decide what the checks ask of a project.

**The profile** says what kind of project it is:

| Profile | Set by | What it means |
| --- | --- | --- |
| `data-science` | `astack new` | Always has the `scientific-review` and `scientific-validation` capabilities: reviews include a scientific-credibility axis, and CHECK also runs your `[validation] verify` commands, or reports VERIFY as `not-run` (never a pass) while none are declared. Strict ship-ready runs declared VERIFY too, and blocks while it is undeclared once the analysis is `material`. |
| `general` | `astack install` | Your language, layout, and check command, unchanged. It can keep either scientific capability without switching profile. |

For a project that is both science and software, pick by its main product:

- **`data-science`** when the main product is results: findings, figures, maps, a paper.
- **`general` with both scientific capabilities** when it is mainly software that also does some
  analysis.
- **plain `general`** when it makes no scientific claims.

Switch later with `astack change-policy` (dry-run first, like `install`). It keeps the same managed
files and checks. See [profile transitions](profile-transitions.md).

In a General project the audit runs only the checks that apply to any repository: managed files,
secrets and large files, private history, a clean worktree, and, with `--run-tools`, your own
`[validation] check` commands. It never asks for `uv.lock`, `pyproject.toml`, or a `justfile`.
The science checks start once the project opts in, through a scientific capability,
`[review] scientific` or `scientific_paths`, `[scientific_outputs]`, an `[analysis]` stage, or a
`data/sources.yml`. Until then they report
`not-assessed`, never `pass`. `astack install` adds no `justfile`, so run the strict audit as
`python .astack/workflow.py audit --strict --run-tools`. Local audit reports can hold
sensitive details, so when nothing ignores `reports/audits/` yet, the audit writes a
`.gitignore` inside that folder and leaves yours alone. If the folder already holds files or
is a link, it reports that instead.

**The analysis stage** (`[analysis] stage`) says whether anyone relies on the results yet:

| Stage | When | What the audit asks |
| --- | --- | --- |
| `exploratory` | The default for `astack new`. You are still looking. | Reproducibility stays `not-assessed`, and ship-ready does not require a reproduction. |
| `material` | A talk, report, paper, or decision will rely on the results. | Declare `[reproduction]` and its outputs. Strict ship-ready needs a `verified` rerun of the current commit ([section 8](#8-reproduction)). |

Switch to `material` before you rely on a result, not after.

## 2. Who owns which file

`astack` keeps a manifest (`.astack/manifest.json`) with the checksum of every file it installed.

| Kind | Examples | Upgrade behaviour |
| --- | --- | --- |
| **Managed** | `.astack/*`, skills in `.opencode/` `.claude/` `.agents/`, `.pi/settings.json` | Replaced only if its current bytes still match the recorded checksum. If you edited it, the upgrade stops and tells you. |
| **Yours** (scaffolded once) | `README.md`, `AGENTS.md`, `pyproject.toml`, `.astack/project.toml`, `data/sources.yml`, `docs/*`, `src/`, `tests/` | Never touched after creation. |
| **Unknown** | Anything `astack` did not create | Never claimed, even if identical to an `astack` file. |

Put your own rules in your project's `AGENTS.md`, not in the skills, so upgrades never collide with
them. Every supported agent reads `AGENTS.md`, Claude Code included, so `astack` does not create a
`CLAUDE.md`.

## 3. Data: the part that matters most for science

```text
data/
  sources.yml      the provenance record: one entry per source
  raw/             source snapshots: never edited, shared across worktrees
  manual/          human-curated inputs, with their curation recorded
  interim/<c>/     reproducible intermediates, per component, worktree-local
  processed/<c>/   analysis-ready products, per component, worktree-local
```

**`data/sources.yml`** is the single, machine-readable answer to "where did this come from and
may I publish it?" Each source records: title, path or URL, origin, provider, access date,
version, license, sensitivity, redistribution rights, allowed derived outputs (for example,
`aggregate-tables` or `derived-figures`), citation, and identity.

- Identity is a SHA-256 of a file (`sha256:<hex>`), of a directory (`sha256-tree-v2:<hex>`), or a
  verifiable external identifier such as an immutable release, a DVC hash, or an object-store
  generation. `python .astack/workflow.py checksum <path>` prints the value to record. The older
  `sha256-tree-v1` is still compared, so a changed directory is still caught, but it blocks release
  (P1) because two different directories can share a v1 digest.
- License, sensitivity, redistribution, citation, and identity each have one of four states:
  `known` (with a value), `not_applicable` (with a reason), `unknown`, or `not_yet_verified`.
  Unknown is allowed: it is honest. But unknown never passes a release check.
- A source that is only a URL must also say how to fetch it (`acquisition` or `retrieval`), and
  its identity must be verified: `python .astack/workflow.py verify-source <id> <file>` hashes
  the bytes you retrieved and writes `data/source-verification/<id>.json`. Without that record,
  the source does not pass release checks.
- Spatial data can declare CRS, datum, extent, resolution, nodata and geometry validity in
  `data/spatial.yml`, with project validators run by `--run-tools`. This is optional.

**Raw is immutable.** The audit rehashes every local source and flags any mismatch as P0. Large
read-only files are cached by their file stamp between audits; strict audits always rehash. Raw
files with no `sources.yml` entry are flagged too. Reproduction and acquisition refuse to finish if
they changed existing raw files.

**Acquisition is separate from reproduction.** `just acquire` runs your declared download step, the
only step allowed to add raw data. `just reproduce` only consumes what is already there. It never
downloads, and never runs `dvc pull`. Acquisition is staged: the command writes new inputs only
to `$ASTACK_ACQUISITION_DIR`, and astack adds them to `data/raw` without copying the existing raw
tree or replacing any existing path. Raw must already be read-only.

**Snapshot by default, verify in place by exception.** Every source is copied into `data/raw` as
a read-only snapshot unless it records `storage: in_place` with `in_place_reason: size` or
`license`. `size` is valid only when the source's total size is above `[data] snapshot_limit_gb`
(default 5, per source). An in-place source names a machine-local root, as in
`path: root:archive/survey.parquet`, where each machine sets `archive` in `.astack/local.toml`
under `[data.external_roots]` or in `ASTACK_EXTERNAL_ROOT_ARCHIVE`. It still needs a SHA-256
`raw_checksum`. The audit rehashes it where it lives, reproduction refuses to run when it is
missing or changed, and the run command gets `ASTACK_EXTERNAL_ROOT_<NAME>` so code can find it.
astack never writes to an external root, so a shared or exFAT drive works as a read-only reference.

**Worktrees.** You can run several agents in parallel Git worktrees. Only `data/raw` is shared, and
only after you make it read-only (`chmod -R a-w data/raw`). In a new worktree, run
`python .astack/workflow.py share-raw` to preview the link, then `--apply`. Everything else,
including interim and processed data, reports, and environments, stays local to each worktree, so
two agents cannot overwrite each other's outputs.

**Outputs** go to per-component folders: `artifacts/<c>/`, `evidence/<c>/`,
`reports/figures/<c>/`, `reports/tables/<c>/`. `scratch/` is disposable and never counts as
evidence.

## 4. Doing the work

`astack` ships no task tracker or lifecycle engine. Work is ordinary Git:

- One coherent change per branch and pull request, linked to a GitHub issue (`Closes #N`). If
  GitHub is unavailable, use one file under `docs/work-items/`.
- Plan larger work with `/to-spec`, then split it with `/to-tickets` (both from
  [Matt Pocock's skills](https://github.com/mattpocock/skills)). They file GitHub issues labelled
  `ready-for-agent`, and each ticket then becomes one branch and PR. Without GitHub they write one
  file per ticket in `docs/work-items/`.
- Issues use a short label set: `bug`, `enhancement`, `documentation`, `ready-for-agent`,
  `upstream` (blocked outside the project), and `needs-decision` (a person must decide first).
  Scientific work is linked to its analysis with an `Analysis: <key>` line, not a label
  ([section 9](#9-scientific-output-contract-optional)).
- In T3 Code, the pinned [tstack](https://github.com/t3dotgg/tstack) skills do the driving:
  `full-stop` (prove, review, and prepare a PR; never merges), `full-send` (merges when every gate
  passes, only when you ask for it), `file-pr`, `babysit-pr`, `sol-loop` (cross-model review), and
  others.
- Matt Pocock's `grill-me` questions your plan in rounds before anything gets built. It hands off
  to his `grilling` skill. Upgrades remove astack's old `grill-with-docs` and `domain-modeling`
  copies only when they are unedited, so your own versions stay.
- `.opencode/skills/human-decisions/` sets when an agent must stop and ask you: merges, publishing,
  remote writes, and anything destructive.

## 5. Skills, and which agent sees them

`astack`'s own skills are written once, in `.opencode/skills/`, and copied to where each agent looks.

| Skill | What it does |
| --- | --- |
| `project-policy` | Entry point: which profile and rules apply to this project |
| `ds-output-contract` | Agree on the scientific outputs (headline figures, tables, validation) before building |
| `pr-visuals` | Attach annotated images to a PR only when a picture proves what text cannot, kept private on a `pr-evidence` branch |
| `source-comments` | After implementation or review fixes, enforce checkable invariants in code, prune stale or narrating comments, and explain only the remaining "why" |
| `pr-review-pass` | Experimental, opt-in, non-blocking PR review pass alongside `sol-loop` that checks `review-policy` and runs security or scientific-robustness lenses (plus optional `REVIEW.md`) only when triggered |
| `ds-repo-audit` | Run the audit and file its result as a GitHub issue (or a local file) |
| `ds-report-generation` | Write a report from a fixed template, with every number linked to its evidence file and checked by `just report-check` |
| `research-visuals` | Create diagrams, scientific figures, captions, and tables, in a report or on their own. New methodology diagrams start with prototypes in the styles that fit the research, for you to choose from now or later. Save project-specific styles in `docs/diagram-styles.md` |
| `publish-public` | Build a clean, allowlisted public export |
| `astack-upgrade` | Safely upgrade `astack` in this project |
| `human-decisions` | Shared ask-a-human policy, used by the others |

The upstream skills (tstack and Matt Pocock's) are pinned to an exact commit and file checksum in
`integrations/t3/upstream.lock.json`. `astack new` and `astack install` write all of them, with
`astack`'s own, to the folders each agent reads, as managed files:

| Harness | Reads skills from |
| --- | --- |
| T3 Code / Codex | `.agents/skills/` |
| Claude Code | `.claude/skills/` (only this folder) |
| OpenCode | `.opencode/skills/`, `.claude/skills/`, `.agents/skills/` |
| Pi | `.agents/skills/`, plus `.opencode/skills/` via `.pi/settings.json` (after you trust the project) |

To copy the pinned skills somewhere outside a project, such as `~/.claude/skills`, use
`astack skills --dest <folder>` (dry-run) and then `--apply`.

## 6. Validation tiers

You declare commands in `.astack/project.toml` under `[validation]`:

| Tier | Run with | Meant for | Default |
| --- | --- | --- | --- |
| **CHECK** | `just check` | Every change: lockfile, format, lint, types, fast tests | `just check` |
| **VERIFY** | `just verify` | Representative multi-step runs on tiny fixtures | not configured |
| **RELEASE** | `just release` | Full reconstruction from raw data | not configured |

A tier you have not configured reports **not configured**. It is never reported as passed. Tests
marked `integration` run in `just integration`, and `just test-split` proves that every test is in
exactly one tier.

## 7. The audit

`just audit-json` (or `just ship-ready` for the strict, human-readable version) gives five verdicts.
`just ship-ready <analysis>` also checks one declared analysis ([section 9](#9-scientific-output-contract-optional)):

| Verdict | Based on |
| --- | --- |
| `workflow_conformance` | Required files, manifest checksums, layout |
| `software_checks` | Whether the configured check and integration commands ran and passed (`--run-tools`) |
| `computational_reproducibility` | Reproduction evidence for a material analysis. Passing software checks never count; exploratory analyses stay `not-assessed` |
| `scientific_credibility` | Always `needs-interpretive-review`: a person must judge the science |
| `release_readiness` | Provenance completeness, licensing, and redistribution, plus the above |

Findings are ranked P0 (data safety, such as a changed raw file) to P3. The `ds-repo-audit` skill
files them as a sanitized GitHub issue. `--ci` runs the limited checks possible in CI, which
cannot see local-only data. `--strict` fails on anything unresolved.

## 8. Reproduction

Set `[analysis] stage = "material"` once results matter. Then declare `[reproduction]` with an
engine (`command` or `dvc`), a component, the command or DVC targets, and the `outputs` the
pipeline must write. `just reproduce` then:

1. refuses to start unless the Git worktree is clean, and refuses if no `outputs` are declared;
2. records the commit, the `sources.yml`, project config and `uv.lock` fingerprints, a digest of
   the raw bytes in `data/raw/` (`inputs`), and the packages and native libraries of the
   interpreter your command launches (`environment`, or `unavailable` for non-Python commands);
3. passes `random_seed`, if declared, to the command as `ASTACK_RANDOM_SEED`;
4. runs your pipeline;
5. fails if the pipeline changed raw data, wrote outside its allowed output folders, changed its own
   inputs or environment, or did not write every declared output;
6. records a digest of each output and writes the run record to `.cache/runs/<component>/`. With
   `--promote`, it also writes a durable copy to `evidence/<component>/runs/`.

The first promoted run is `command-succeeded`: it becomes the reference. A later run is `verified`
only if its outputs match that reference, by digest or by a project tolerance check declared under
`[reproduction.tolerance]`. Otherwise it is a `mismatch` and is never promoted. Strict `ship-ready`
requires a verified record for the current commit, and `just reproduce --verify` fails unless the
rerun verifies.

**Which outputs are current.** `just outputs` labels each output `current`, `outdated`, `missing`
or `unknown`, with the reason. In a DVC project, an output is current when its stage's deps,
parameters and outputs still match `dvc.lock`, as `dvc status` reports. A dep left out of
`dvc.yaml` cannot make an output outdated, so the scaffolded `dvc.yaml` lists the whole package,
the stage's script and `uv.lock`, and outputs use `cache: false` so the pipeline never stores data.
astack asks DVC for its resolved pipeline, so foreach and matrix stages, `wdir` and `.dvcignore`
count as DVC sees them, and a file inside a directory output counts only if DVC tracks it. A
frozen stage's outputs are `unknown`, because DVC does not check its inputs. A stage that reads a
file under `evidence/`, `artifacts/`, `reports/figures/`, `reports/tables/`, `data/interim/`,
`data/processed/` or `scratch/` that no stage declares as an output is `unknown` too, because
nothing records how that file was made. A folder dep there counts only when every file in it
belongs to a declared output. Staleness carries down the pipeline, and `unknown` wins.

**What each stage reads and writes.** In a DVC project, `just reproduce` runs the stages one at a
time, in pipeline order, and checks each one against `dvc.yaml`:

- Writes are checked for every file that persists. astack compares the project's files before and
  after the stage, including each file's change time, which programs cannot set, so any file the
  stage left created, changed or deleted outside its declared `outs` is an undeclared write,
  whatever program wrote it. A file created and removed within one stage cannot reach a later one.
- Reads of derived files are enforced for every program. While a stage runs, each file under
  `data/interim/`, `data/processed/`, `evidence/`, `artifacts/`, `reports/figures/` and
  `reports/tables/` that the stage does not declare as a dep or out is made unreadable, then
  restored. A read by pyarrow, GDAL, netCDF, R or a shell tool that the stage starts fails the
  stage with a message naming the file to declare. A process that opened the file before the
  stage began, such as a long-running server the stage talks to, is not affected. A process the
  stage leaves running, such as a worker holding a writable memory mapping, can still change
  files after the check, because the snapshot compares file metadata, not bytes. `data/raw` is
  never locked, so declare it by folder. Symlinks and hard-linked files are never changed,
  because that would change a file elsewhere, possibly a shared cache. They, a file an ACL keeps
  readable, and every file when running as root, are lock gaps, reported as `inputs-not-locked`
  (P2). Locked files are recorded with their identity in `.cache/input-lock.json` first, and the
  next run restores anything an interrupted run left locked, leaving a replaced file alone.
- Other reads are checked where Python opens or imports a file. A hook loaded into every Python process
  the stage starts records them, and any project file outside the stage's `deps` is an undeclared
  read. A `.pyc` read counts as its source file. Files that compiled libraries open directly
  (pyarrow, GDAL, netCDF and similar) are not seen, nor is anything a non-Python program reads.

Any toolchain works. R scripts, shell steps, compiled tools and scripts run by name all run as
ordinary DVC stages: their outputs are labeled from the deps and outs in `dvc.yaml`, and their
writes are checked. A stage whose command is not a single Python process (a script whose first
line starts Python counts), or that starts other programs, is reported as untraced, a P2
disclosure. Its outputs keep their DVC label, and `report-check` notes them as
`reads-not-checked` without failing. Declare such a stage's deps carefully, because nothing
checks its reads. Scratch files a toolchain leaves behind, such as R's `.Rhistory` or
`Rplots.pdf`, can be listed in `[reproduction] check_ignore` as project-relative patterns. They
can never cover `data/raw`.

An undeclared write or read is a P1 finding, and the stage's outputs, and everything downstream,
become `unknown`. A check counts only for the exact run it
describes, so a stage last run by a plain `dvc repro` is reported as unchecked (P2) until
`just reproduce` runs it. The results are kept in `.cache/dependency-check.json`. In a command project,
an output is current while its run record still matches, and any committed change other than a
run record makes it outdated. Files under `reports/figures/`, `reports/tables/` and `artifacts/`
that no stage or run declares are `outdated`, with the reason "no run record". `unknown` means
DVC could not report, which never counts as current.

## Reviewing a workspace

Pull requests review each change: comments, requested changes and the agent's follow-up all stay
in GitHub. The review notebook does what a pull request cannot: a person reads the pipeline step
by step, as a whole, and signs off what they read. Several pull requests can each look right
while together they are wrong, for example two that both convert the same input. It shows only
that person's work. Output labels (`just outputs`) and audit findings (`just audit-json`) are for
agents.

`just review` opens `notebooks/review.py` read-only, with code hidden.

**What gets signed off.** Each pipeline step, once:

- A DVC stage is a step. `foreach` and `matrix` items are one step, because they run the same
  code. A table lists every item, and a warning names any item whose command differs.
- A code file used by two or more steps is **shared code**, signed off once on its own page. A
  change to it asks for one review, not one per step.
- A step's code is its declared deps that are not data, plus its resolved commands and the values
  of the parameters it declares. A change to another step's parameter does not reset it. `uv.lock`
  is left out.
- A project without DVC has one step: its reproduction command and tracked `src/` and `scripts/`.

**Overview.** A clickable pipeline map, drawn from DVC's graph. Hovering a box highlights what
feeds it and what it feeds. Below it, a table of what needs a sign-off. **Sign off selected**
covers only items opened this session, and only the code as it was when opened.

**Step page.** Purpose (the script's docstring), upstream and downstream links, inputs,
parameters, commands, code, and each output's label. A step signed off before shows its changes
in context with the full file one click away, plus the lines that use any changed parameter. A
change upstream or in shared code is a note, and does not reset the step.

**Statuses.** Recomputed every time: `reviewed`, `changed-since-review`, `unreviewed`, `revoked`,
`invalid-record` or `unknown`. A missing dep, a missing parameter, a symlinked folder, or a
dependency check that saw undeclared reads makes a step `unknown`, which never counts as signed
off.

**Signing off.** Writes `.astack/reviews/<step>.json` with the reviewer, the full commit, what it
covered and a checksum. If the code changed after the page loaded, the sign-off is refused. Only
committed code can be signed off: HEAD must store every code file, and the files declaring its
command and parameters, byte for byte. A symlinked code file must become a regular file first.

**Revoking.** Untick a signed-off step, optionally say why, and confirm. The record becomes a
revocation that keeps the old sign-off inside it, the step shows as revoked, and RELEASE blocks
until someone signs it off again.

**What a diff can be trusted for.** Changes replace a full read only when they provably cover
everything: the record lists what it covered, its code was committed, that commit is in this
branch's history, and every file is tracked and not a symlink. The diff compares the bytes git
stored with the bytes on disk, built by astack rather than `git diff`, so no filter, diff driver
or line-ending setting changes what is shown. Carriage returns show as `␍`, tabs as `⇥` and spaces
at a line's end as `␣`. A file containing those markers, or invisible, control or line-separator
characters, needs a full read, where such characters show as their code point. Code is always
escaped, never rendered. Git replacement objects are ignored. Each file shows at most 1,500 lines
and says so when cut.

Only a person signs off. Agents are told never to call `record_review` or `revoke_review`, or
write under `.astack/reviews/`, and a record an agent writes shows up as a changed file in the
next pull request. Nothing local can prove who clicked, so a sign-off is attested by the git user,
not verified.

Viewing writes nothing in the project: no markers and no DVC cache configuration. DVC may still
keep its own temporary files under `.dvc/tmp`, which Git ignores. An unchanged copy of the review
notebook does not count as notebook-only analysis. An edited one does.

A missing sign-off is human work, so it blocks only the RELEASE tier, through
`review-status --require-reviewed` in `[validation] release`. CHECK runs `just review-check`
(`marimo check`).

Maintaining the notebook: see [maintaining-the-review-notebook.md](maintaining-the-review-notebook.md).

## 9. Scientific output contract (optional)

For work that leads to a paper, `ds-output-contract` has the agent propose, before building: the
research question, required data products, headline figures, maps and tables, required
validation, and optional extras. You confirm it. The confirmation is stored against a digest of the
definition, so if the definition changes materially, you must confirm again.

The contract lives in your repository: definitions in `docs/scientific-outputs.yml`, progress and
confirmations in `evidence/scientific-outputs.yml`. Outputs are grouped into **analyses**, each a
named set of outputs one line of work delivers, and one analysis can span many issues. An issue or
PR that creates or changes declared outputs carries one `Analysis: <key>` line, and
`just ship-ready <key>` fails if that key is not declared or its confirmation is out of date.
`python .astack/workflow.py output-contract --analysis <key>` prints what an analysis must deliver.
See `docs/scientific-outputs.md` in a generated project for the file format.

## 10. Publishing

Never make a private repository public: its history keeps everything you ever deleted. The
`publish-public` skill builds a new, clean export from an explicit allowlist, using
`python .astack/workflow.py export-public --out <dir>`, which reads committed files only. `data/sources.yml`
decides what may leave, through `redistribution` and `allowed_outputs`. The export excludes Git
history, raw and sensitive data, internal notes, caches, and notebook outputs. It is scanned for
secrets, and its tests run before anything is pushed. Creating or pushing the public repository
always needs your explicit approval.

## 11. Upgrading

From inside a project, run `python .astack/workflow.py upgrade vX.Y.Z`, or prefix it with
`uv run --locked` in a uv project. This fetches that exact published release and runs that
release's installer as a dry run. Apply with the printed digest. Edited managed files block the
upgrade instead of being overwritten. An older `astack` never rewrites a project set up by a newer
one. Interrupted applies leave a journal and block the next install until it is resolved
(`astack install --recover`).

## 12. Command reference

**`astack` CLI**

| Command | Purpose |
| --- | --- |
| `astack new` | Create a data science project (asks for missing details in a terminal) |
| `astack install` | Adopt or upgrade an existing repository (dry-run by default) |
| `astack change-policy` | Switch profile (`general` / `data-science`) or capabilities |
| `astack doctor` | Read-only check that a project's tools and skill files are in place |
| `astack skills` | Verify the pinned skills, or copy them to a folder outside a project |

**In a project** (`just <target>`, each a thin wrapper over `.astack/workflow.py`)

| Target | Purpose |
| --- | --- |
| `check`, `integration`, `test-split` | Fast checks, integration tests, test-tier proof |
| `test`, `typecheck`, `format` | Pieces of `check`, one at a time |
| `verify`, `release` | The VERIFY and RELEASE tiers |
| `audit-json`, `ship-ready [<analysis>]`, `ci-check` | Audit (JSON / strict text / CI-limited) |
| `acquire`, `reproduce` | Fetch raw inputs / rerun the pipeline and record it (`reproduce --verify` requires a match) |
| `report-check` | Check that report figures and cited numbers point at real, current evidence |
| `outputs` | Label each pipeline output current, outdated, missing or unknown, with the reason |

Project runtime commands with no `just` target:

| Command | Purpose |
| --- | --- |
| `python .astack/workflow.py upgrade <tag>` | Plan an upgrade to an exact published release (dry-run) |
| `python .astack/workflow.py validate <tier>` | Run one declared validation tier |
| `python .astack/workflow.py output-contract [--analysis <key>]` | Print the declared scientific output contract |
| `python .astack/workflow.py file-audit` | File an audit report as a GitHub issue, or keep it local |
| `python .astack/workflow.py share-raw [--apply]` | Link the shared raw inputs into a linked worktree |
| `python .astack/workflow.py dvc-status`, `dvc-cache` | DVC pipeline status and cache cleanup (DVC projects only) |
| `python .astack/workflow.py report-check [reports...]` | Check report figures and cited numbers against evidence (`just report-check`) |
| `python .astack/workflow.py outputs` | Label pipeline outputs current, outdated, missing or unknown (`just outputs`) |
| `python .astack/workflow.py review-status [--require-reviewed]` | What the review notebook shows: each pipeline step and its sign-off; `--require-reviewed` fails unless every component's code is signed off as it is now |
| `python .astack/workflow.py review-policy [--root <dir>] [--base <ref>] [paths...]` | Classify changed or explicit paths for risk floor, `protected_paths`, `scientific_paths`, `required_axes`, and active lenses |
| `python .astack/workflow.py checksum <path>` | Print the `raw_checksum` value for a file or directory |
| `python .astack/workflow.py verify-source <id> <file>` | Hash bytes retrieved for an external source and write its verification record |
| `python .astack/workflow.py export-public --out <dir>` | Write an allowlisted, history-free public copy of the committed project |

## What the checks prove, and what they don't

`astack`'s rule is that missing, skipped, or unavailable evidence never counts as a pass. To hold
itself to that rule, here is exactly what a green result means.

| A green... | Proves | Does **not** prove |
| --- | --- | --- |
| `just check` | The code is formatted, typed, and its fast tests pass | Anything about the science |
| raw checksum | The local snapshot is byte-identical to what `sources.yml` recorded | That the source itself is correct or fit for purpose |
| external source `verification` record | The recorded bytes were retrieved from that URL and hashed | That the upstream will serve the same bytes again |
| `software_checks: pass` | The configured check and integration commands ran and passed | Anything about reproduction |
| `computational_reproducibility: pass` | A material analysis has valid, verified reproduction evidence | That the outputs are scientifically correct |
| `just reproduce` → `command-succeeded` | The pipeline ran from a clean commit, did not touch raw data, and wrote its declared outputs, whose digests are recorded | That a second run gives the same outputs |
| `just reproduce` → `verified` | A promoted rerun matched the promoted reference by digest, or by a declared project tolerance check | That the outputs are scientifically correct |
| `random_seed` in a run record | The seed was passed to the command as `ASTACK_RANDOM_SEED` and recorded | That the pipeline used it. Only a verified rerun shows that results repeat. |
| `inputs` in a run record | The exact raw bytes present when the pipeline ran | That they are the right data |
| `environment` in a run record | The packages and native libraries of the interpreter the command launched | The environment of non-Python commands (`unavailable`) |
| `data/spatial.yml` + validators | The declared spatial metadata is complete and the project's validators passed | That the CRS, resolution or resampling suits the question |
| `[publication]` metadata | License, citation and availability statements exist, are complete, and any DOI is well formed | That the DOI resolves to this release's deposit, or that the licences are compatible |
| `export-public` | Only allowlisted, committed, source-permitted, secret-scanned files were exported, without history | That the exported science is correct, or that free-text licence terms were read correctly |
| `just report-check` | Every image, and every link whose visible text is a number, points at an existing file under `evidence/`, `artifacts/`, `reports/figures/` or `reports/tables/`, each JSON-pointer value matches at the decimals shown, and a final report has no `[UNRESOLVED` marker. A cited outdated or missing output fails any report. A final report also fails on a file no recorded run produced, or whose state DVC could not report. A draft lists those in `evidence_notes`. A cited run record under `evidence/**/runs/` must be a current one. Reports are Markdown only: any raw HTML other than comments fails, and so do Pandoc bracketed spans, raw `{=html}` blocks and attributes, Quarto shortcodes, image, link or inline-code attributes other than `width`, `height`, `#id` and `.class`, and a figure or cited-number path with `//`, an encoded separator or a `..` after a folder name | That unlinked numbers or prose are supported, that figures generated by running `.qmd` code cells are checked (save them under `reports/figures/` and embed them instead), or that the science is right |
| output contract `confirmed` | You approved this definition of the outputs | That the outputs meet it |
| `scientific_credibility` | Nothing. It is always `needs-interpretive-review`. | |

Design, sampling, leakage, spatial dependence, uncertainty, and whether a method suits the
question are the reviewer's job, and yours. `astack` makes them reviewable. It does not judge them.
