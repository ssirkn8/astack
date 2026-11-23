---
name: astack-upgrade
description: Compare and safely upgrade a workspace to a selected astack release without overwriting project-owned or locally customized files.
metadata:
  permission-level: workspace-write
---

# astack Upgrade

For human-response decisions, apply `.opencode/skills/human-decisions/SKILL.md`.

## Use when

Use inside an existing astack project when an explicit published workflow release is requested,
including when `/ds-repo-audit scope=workflow` reports version or managed-file drift.

## Goal

Reconcile only astack-managed workflow files through a dry-run-first, checksum-aware upgrade.

## Procedure

1. Inspect target Git status and current `.astack/version` and `.astack/manifest.json`.
2. Require an exact published stable tag, such as `v1.2.1`. Run
   `python .astack/workflow.py upgrade <tag>` without `--apply` from the project.
   If its older runtime lacks this command, run that release's installer directly:
   `uv tool run --from git+https://github.com/ssirkn8/astack.git@<tag> astack install . --release <tag>`.
   Both verify published release evidence, resolve the tag to a commit, and run that exact
   commit's `astack install`. Never use main, another moving branch, an arbitrary commit, or the
   project's older managed-file implementation as the upgrade authority. Missing or unpublished
   release evidence, a project recorded by a newer astack, an interrupted installation, and a dirty
   worktree are blockers.
3. Review the planned creates, updates, preserved customizations and conflicts.
4. Stop if the upgrade would change project-owned files, managed files were customized, the target
   is dirty, or the intent is unclear.
5. Present the exact dry-run plan and ask for apply authorization with the native question tool
   unless the current user request already explicitly authorizes that exact plan. If the plan differs
   materially from the requested upgrade, ask about the changed plan. A declined answer stops the
   upgrade. A canceled, rejected, or unavailable question is not approval. For a required answer,
   ask in text and stop. After explicit authorization, rerun the same command with
   `--apply --approve-plan <plan_digest>`. Changed project content, release commit, or managed
   actions invalidate the digest and require a fresh dry-run and approval.
6. The installer writes the version marker and manifest last, after every other managed file.
7. After apply, run the project's checks with `python .astack/workflow.py validate check`, then
   `python .astack/workflow.py audit --run-tools --strict`. Report failures as failures, not a
   healthy upgrade.
8. Do not commit, push, modify research code, or change data.

## Output

Return source and target versions, dry-run plan, applied files, preserved files, conflicts, validation, and next action.
