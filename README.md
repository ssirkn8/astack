# astack

Agent skills and checks for software and data science, for solo developers and small teams. They
work in [T3 Code](https://github.com/pingdotgg/t3code) first, and also in Claude Code, Codex,
OpenCode and Pi.

astack pins proven skills from [tstack](https://github.com/t3dotgg/tstack) and
[Matt Pocock](https://github.com/mattpocock/skills), then adds what data science needs: raw data
that never changes, a record of every source, and checks that never count missing evidence as a
pass.

## Skills

| Skill | What it does |
|---|---|
| `ds-output-contract` | Agrees the headline figures, tables and validation with you before building. |
| `ds-repo-audit` | Runs the audit and files the result as a GitHub issue. |
| `ds-report-generation` | Writes a report whose every claim traces to evidence in the repository. |
| `research-visuals` | Creates diagrams, scientific figures, captions and tables, in a report or on their own. |
| `pr-visuals` | Attaches annotated images to a PR when a picture proves what text cannot. |
| `source-comments` | Keeps code comments useful to a human reviewer, with no comment quota. |
| `pr-review-pass` | Experimental, opt-in, non-blocking PR review pass alongside `sol-loop` for protected or scientific paths. |
| `publish-public` | Builds a clean, allowlisted public copy without private history. |
| `astack-upgrade` | Upgrades astack in a project without overwriting your edits. |
| `project-policy` | Tells the agent which profile and rules apply to the project. |

Projects also get the pinned upstream skills, such as `full-stop`, `sol-loop`, `grill-me` and
`to-spec`, each locked to an exact commit and checksum.

## Install

```sh
uv tool install 'git+https://github.com/ssirkn8/astack@v0.1.0'

astack new my-study            # asks for a name, description and intended use
cd my-study
git init -b main && git add -A && git commit -m "Start from astack"
just check                     # lint, types and tests
just ship-ready                # the full audit to run before each PR
```

Then open the folder in your agent. To add astack to a repository you already have, see
[General installation](docs/general-installation.md).

## Requirements

- Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), [just](https://just.systems) and Git.
- An authenticated [GitHub CLI](https://cli.github.com) (`gh`) for the PR skills.
- A second model provider for `sol-loop`'s cross-model review. T3 Code provides one.

## What the checks prove

That the evidence you declared exists and is consistent. Not that an analysis is correct: that
needs a person, and the audit says so.
[Details](docs/how-astack-works.md#what-the-checks-prove-and-what-they-dont).

## Make it yours

Put your own rules in your project's `AGENTS.md`, not in the skills, so upgrades never overwrite
them.

## Docs

- [How astack works](docs/how-astack-works.md): every moving part and every command.
- [General installation](docs/general-installation.md): existing repositories, and what each agent needs.
- [Profile transitions](docs/profile-transitions.md): switching between General and Data Science.
- [Validation](docs/validation.md): the CHECK, VERIFY and RELEASE tiers.
- [Scientific audit](docs/scientific_audit.md): what the audit checks, and what needs a person.
- [Local-only CI](docs/local-only-ci.md): CI when your data cannot leave your machine.
- [Public release policy](docs/public_release_policy.md): why releases are clean exports.
- [Maintainer releases](docs/maintainer-releases.md): PR titles and version proposals for astack itself.

## License

astack is [MIT](LICENSE). The upstream skills keep their own MIT licenses, in
`integrations/t3/licenses/`. Report security issues as described in [SECURITY.md](SECURITY.md).
