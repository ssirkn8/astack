---
name: ds-output-contract
description: Propose, confirm, and review a lightweight scientific output contract for material analytical work.
metadata:
  permission-level: workspace-write
---

# DS Output Contract

For human-response decisions, apply `.opencode/skills/human-decisions/SKILL.md`.

## Use when

Use before substantial implementation for material scientific work, and revisit it when
declared outputs or validation change.

## Goal

Make expected scientific deliverables explicit without replacing scientific judgment with a rules
engine.

## Procedure

Scientific outputs live in one project-level `[scientific_outputs]` registry with a separate
evidence file, as described in `docs/scientific-outputs.md`. Projects without a declaration stay
supported and report `not_declared`.

1. Start from the research question, intended use, and decision context. These remain authoritative.
2. Propose conventional, human-meaningful, publication-style outputs for the analysis type. When
   conventions or validation practices are unclear, perform bounded literature reconnaissance and
   record the references and rationale. Literature informs the proposal but does not dictate it.
3. Record definitions in the registry: stable output IDs with `type`, `role`, description, required
   `validation` IDs, and optional communication intent, plus an `analyses` mapping that lists which
   outputs (and machine evidence) each analysis contributes. Several analyses may share an output.
   Keep status and evidence out of definitions; they belong in the evidence file.
4. Derive one analysis's contract with
   `python .astack/workflow.py output-contract --analysis <analyses-key>`, and the whole project
   without `--analysis`.
5. Present the derived headline outputs and required validation, then use the native question tool
   to ask the human to confirm or amend them before substantial implementation. Keep a free-form
   answer available for scientific explanations. A declined answer stops the affected work. If the
   question is canceled, rejected, or unavailable, ask in text and stop. Do not infer confirmation
   from silence or a tool-permission response.
6. After explicit confirmation, persist
   `confirmations.<analysis>: {definition_digest: <digest>, evidence: <explicit human decision>}`
   in the evidence file, using the `definition_digest` from the summary. The digest binds outputs and
   required validation, not progress or evidence paths. Reuse unchanged confirmations across
   sessions. Material definition changes require renewed confirmation. This scientific confirmation
   is distinct from execution approval, even when one explicit decision covers both.
7. Name the analysis in the work. An issue or PR that creates or changes declared outputs carries
   one `Analysis: <analyses-key>` line (at most one analysis; one analysis may span many issues;
   never a label), and refers to output IDs rather than copying definitions. Confirm the analysis
   while writing the spec, and commit its registry entries with that confirmation in the first
   ticket's PR; later tickets reuse it until its outputs or required validation change. Before
   opening each such PR, run `just ship-ready <analyses-key>`: it fails when the key is not in
   the registry or its confirmation is stale. Engineering-only work names no analysis.
8. Prioritize required correctness, declared deliverables, required validation, and reproducibility
   before optional analyses or presentation extras. Deadline pressure never permits required
   validation to be skipped or deferred.
9. Record new ideas as follow-up issues rather than silently expanding the registry.
10. At completion, verify every declared evidence path and report incomplete required items,
   unresolved blocks, and interpretive review needs honestly.

## Boundaries

- Do not build a rules engine for scientific judgment.
- The contract is project-owned scientific metadata, not managed workflow state or a duplicate work
  tracker.
- Deterministic checks validate declared structure, statuses, safe paths, and evidence presence.
  They never decide which figures, methods, or scientific conclusions are appropriate.
- Existing projects without a declared contract continue to work. Once declared, malformed or
  incomplete required content is a blocking audit finding.

## Output

Return the registry path, the analysis, proposed or confirmed headline outputs, required
validation, literature rationale, evidence status, follow-ups, and blockers.
