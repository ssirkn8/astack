# Adopt astack in an existing software project

General installation adds the shared workflow without generating a Python application or changing
application source, README, CI, dependencies, lockfiles, data, or Git history. `astack new`
writes a Data Science project template, then the same managed files and manifest, and runs
`git init` unless the target is already inside a Git repository or you pass `--no-git`. Every project
upgrades through `astack install`.

## Use a pinned release

Run the installer from the verified commit of an exact published release that supports General.
The installer verifies release publication, resolved commit, and its own source identity. A modified
checkout or a runtime from a different commit cannot claim that published release. Do not infer
publication from the source version string.

```sh
uv tool run --from 'git+https://github.com/ssirkn8/astack.git@<verified-release-commit>' \
  astack install /path/to/project --release vX.Y.Z --check-argv '["npm", "test"]'
```

The default is a dry-run. Inspect every proposed path, source identity, ownership decision, conflict,
and plan digest. Apply with the same arguments plus `--apply --approve-plan <digest>`. Changed source,
Git state, checks, or managed bytes invalidate the digest. Apply refuses dirty or conflicting states.
No automatic stash, reset, dependency installation, CI rewrite, or application migration occurs.

The installer uses Python and its own packaged dependencies. These are workflow tool requirements,
not application dependencies. Ordinary work in an installed General project uses Python's standard
library. DVC metadata needs YAML support, and unavailable evidence blocks preservation decisions.
General CHECK executes the project's exact argv arrays. The installed
runtime and native discovery skills are self-contained for ordinary work. A pinned external
tool is used only for installing/upgrading the workflow, without a separate maintainer checkout.

## Empty destinations and owner instructions

Use the same command with `--initialize` for an empty destination. It initializes Git and installs
workflow files, without an application starter template. Review and commit initial workflow files
before starting other work. A newly initialized Git repository is preserved if
installation fails, while transaction-owned file changes are rolled back.

New workflow files live in `.astack/`, with skill entrypoints in `.opencode/`, `.agents/`,
`.claude/`, and `.pi/`. The pinned upstream skills are managed files too, in both `.agents/skills/`
and `.claude/skills/`, with their licenses in `.astack/notices/`. Existing files at managed destinations are conflicts unless the installed
manifest proves ownership and the current checksum matches. Identical unowned files are not silently
claimed. Owner-modified files remain preserved. Unknown retired components are retained.

Project instructions live in one file, `AGENTS.md`, which every supported harness reads (Claude
Code included). When it is missing, installation proposes minimal workflow guidance there. It is
owner-owned and never automatically replaced during upgrades. astack does not create `CLAUDE.md`;
an existing one stays yours and is left alone.

Projects created before this kept `CLAUDE.md` as a copy of `AGENTS.md`. To stop maintaining both,
first move anything that is only in `CLAUDE.md` into `AGENTS.md`. Then delete `CLAUDE.md`, or
replace its contents with the single line `@AGENTS.md` if you use a Claude Code version that does
not read `AGENTS.md` yet.
Project configuration is also owner-owned after creation. Install does not silently change its checks.

## Skills in each harness

Each harness finds skills in its own folder, so astack writes the same pinned bytes to each:

| Harness | Reads skills from | Notes |
| --- | --- | --- |
| Claude Code | `.claude/skills/` | Only this folder. |
| Codex | `.agents/skills/` | |
| OpenCode | `.opencode/skills/`, `.claude/skills/`, `.agents/skills/` | |
| Pi | `.agents/skills/`, plus paths in `.pi/settings.json` | Only after you trust the project. |

`astack doctor --harness claude-code` (or `codex`, `opencode`, `pi`) checks the required tools,
skill files, and configuration for that agent. It cannot see what the agent actually loads, so
confirm discovery by asking the agent to list its skills.

Some skills also need features from the harness itself, which no file can supply:

- `grill-me` is the only grilling command. It is a short alias that calls the `grilling` skill
  through a Skill tool. Claude Code and OpenCode have one. In Codex or Pi, ask for `grilling` by
  name instead.
- `grilling` hands fact-finding to a fresh sub-agent. Where the harness has no sub-agents, the
  agent looks the facts up itself.
- `to-spec` and `to-tickets` run only when you ask for them by name. They expect a configured
  issue tracker; a project's AGENTS.md supplies it (GitHub issues, `ready-for-agent`), so
  upstream's `setup-matt-pocock-skills` is not needed and not installed.
- `full-stop`, `full-send`, `babysit-pr` and `sol-loop` are written for T3 Code. Outside it,
  `babysit-pr` waits with its bundled `scripts/pr-wait`, and `sol-loop` needs some way to run a
  second model, or it stops and says so.

To use the pinned skills outside a project, for example in `~/.claude/skills`, preview with
`astack skills --dest <folder>` and copy with `--apply`.

## Upgrade and recovery

Projects use `python .astack/workflow.py upgrade vX.Y.Z` (in a uv project, prefix it with
`uv run --locked`). The bootstrap resolves the exact published source and runs that release's
installer. Apply requires its current conflict-free dry-run plan. An older astack leaves a project
set up by a newer one unchanged; roll back with Git instead.

Each apply uses a short-lived exclusive installation lock and a transaction journal. Code bytes are
verified before finalizing `.astack/version` and the manifest. Ordinary errors restore prior bytes
and permissions. Interruption leaves evidence and blocks another normal install.

Use `astack install /path/to/project --recover` to inspect recovery. Apply with its exact digest.
Recovery restores only recorded workflow paths, preserves independent application edits, and refuses
changed workflow files, unknown ownership, or a live/unknown lock owner. It does not erase app data or
Git history. Windows lock-owner liveness is unverified and therefore blocks automatic lock recovery.
Mac results do not certify Windows or Linux behavior.

## Development candidates

Maintainer testing before publication must use explicit `--candidate`, which records candidate status
and a payload digest without claiming a published release. Use disposable consumers. Candidate mode
is not release evidence or authorization to migrate live consumers.
