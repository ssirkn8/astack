---
name: publish-public
description: Build a clean, allowlisted public export without exposing private data, internal notes, secrets, restricted sources, or private Git history.
metadata:
  permission-level: workspace-write-or-explicit-remote-write
---

# DS Publish Public

For human-response decisions, apply `.opencode/skills/human-decisions/SKILL.md`.

## Use when

Use for an explicitly requested portfolio, open-source, archival, or replication release.

## Goal

Create a new clean export or new repository. Never use a branch in a private repository as a sanitization boundary because prior history remains reachable.

## Procedure

1. Establish destination, visibility, ownership, and source redistribution authority. Use the native
   question tool for any missing bounded choice, including whether to create or push a remote
   repository, and state the publication consequence. Keep free-form answers available. A declined
   choice stops publication. If the question is canceled, rejected, or unavailable, ask in text and
   stop. A question response does not override the source allowlist or license checks.
2. Treat `data/sources.yml` as the release authority. Stop when source redistribution or the
   requested derived output type is unknown, prohibited, or absent from `allowed_outputs`.
3. Declare the allowlist in `.astack/project.toml` under `[public_export]` (`include`, optional
   `exclude`, and optional `scan` commands, where `{out}` is the export directory). Include only
   approved code, environment metadata, methods, reports, figures, source summaries, license,
   citation, and reproducibility instructions.
4. Build the export with the deterministic exporter, never by copying files by hand:
   `uv run --locked python .astack/workflow.py export-public --out <new empty directory outside the project>`.
   It refuses a dirty or untracked tree, symlinks, and submodules. It copies only allowlisted
   tracked files from the committed tree, without Git history. It never exports work items, audits,
   scratch, caches, raw data, or local state. Data and derived outputs need known redistribution and
   `public-export` in `allowed_outputs` for the covering source, or for every source when the product
   is derived. It runs the built-in secret scan and every declared `scan` hook, and leaves nothing
   behind on failure. Report its `export_sha256` and file list.
5. Review the export for sensitive content the scans cannot recognize, such as notebook outputs,
   private paths, and unapproved large artifacts. Narrow the allowlist and rebuild rather than
   deleting files from the export.
6. Do not treat a successful export as source-license clearance beyond what `data/sources.yml`
   records.
7. Validate setup and tests in the export when code is included.
8. Create or push a new repository only after explicit authorization for that destination and
   visibility. Do not treat tool permission or an unanswered question as authorization.

## Output

Return destination, allowlisted and excluded assets, source-license verdict, checks, known reproducibility limitations, and publication status.
