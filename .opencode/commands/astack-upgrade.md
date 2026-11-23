---
description: Dry-run and safely apply an astack upgrade
---

Read `AGENTS.md` and `.opencode/skills/astack-upgrade/SKILL.md`, then follow that skill exactly.

Arguments: `$ARGUMENTS`

Require an exact published stable release tag, such as `v1.2.1`, from the project checkout.
Never resolve a missing target to main, latest, or a moving branch.

End with the skill's Output section as a short Markdown summary: outcome, blockers, evidence,
and next action. Missing, skipped, or unavailable evidence never counts as passing.

Default to dry-run. Apply only after the plan is reviewed and explicitly approved.
