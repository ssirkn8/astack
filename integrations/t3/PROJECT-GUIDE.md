# astack project guide

Use upstream skills unchanged. Explicit full-send permits merging when upstream's gate passes.
Full-stop leaves the PR open and unmerged. Neither grants release publication, production,
destructive action, or protection bypass authority. Respect the user's current task exclusions.
Grilling is opt-in and keeps its upstream questioning behavior.

Use native T3 Git/worktrees, delegate capabilities and parent-owned PR watches. Keep GitHub
Issues when useful, but an issue is not required for every direct request. Stop on missing
provider/tool capabilities or required checks, and report the limitation without substitutions.

Run the project's actual checks, for example `just check`, `just integration`, and
`just ship-ready` in a generated project. Passing local tests do not establish passing hosted
checks. Inspect exact current commit/check identity before readiness decisions.

For science: preserve immutable raw source identity and machine-readable `data/sources.yml`,
project-owned General/DS validation, meaningful output contracts, and scientific interpretation.
Missing acquisition, provenance or validation evidence never passes. Optional DVC/Quarto/Marimo
stay optional. Shared worktrees share only immutable raw data, not mutable outputs/environments.

Preserve customized files, local state, active worktrees and original Git history. Installation
and migration preview first, replace only checksum-owned unchanged files, and stop on conflicts.
Public release uses an independently audited allowlisted clean export and separate owner GO.

This is a project-owned guide template, not an upstream skill wrapper. Do not automatically
replace an existing AGENTS.md with it. Legacy installations require an explicit migration first.
